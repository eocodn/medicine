package com.medicine.android

import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.FileOutputStream
import java.net.URI
import java.security.MessageDigest
import javax.net.ssl.HttpsURLConnection

class HttpsReferenceReleaseSource(
    baseUrl: String,
    private val verifier: ReferenceManifestVerifier,
    private val contractMajor: Int = ReferenceRuntimePolicy.CONTRACT_MAJOR,
) : ReferenceReleaseSource {
    private val baseUri: URI = URI(baseUrl).also { uri ->
        require(uri.scheme == "https") { "reference update base URL must use HTTPS" }
        require(!uri.host.isNullOrBlank()) { "reference update base URL must have a host" }
        require(uri.query == null && uri.fragment == null) {
            "reference update base URL cannot have query or fragment"
        }
        require(uri.path.endsWith("/")) { "reference update base URL must end with /" }
    }

    init {
        require(contractMajor > 0) { "reference contract major must be positive" }
    }

    override fun fetchLatest(): VerifiedReferenceRelease {
        val envelopeText = try {
            fetchSmallText(ReferenceReleaseProtocolV2.ROOT_KEY)
        } catch (error: Throwable) {
            val stage = if (error is ReferenceManifestHttpStatusException) {
                "manifest_http_${error.statusCode}"
            } else {
                "manifest_http"
            }
            throw ReferenceManifestStageException(stage, error)
        }
        val envelope = try {
            JSONObject(envelopeText)
        } catch (error: Throwable) {
            throw ReferenceManifestStageException("manifest_json", error)
        }
        // The signature contract is strict on required security fields but does
        // not reject additive server metadata which is outside the signed-frame
        // interpretation used by this client.
        val verified = try {
            verifier.verify(
                envelopeVersion = envelope.getInt("envelope_version"),
                algorithm = envelope.getString("algorithm"),
                keyId = envelope.getString("key_id"),
                releaseSequence = envelope.getLong("release_sequence"),
                payloadBase64 = envelope.getString("payload_base64"),
                signatureBase64 = envelope.getString("signature_base64"),
            )
        } catch (error: Throwable) {
            throw ReferenceManifestStageException("manifest_signature", error)
        }
        return try {
            ReferenceReleaseProtocolV2.parseVerifiedRoot(
                verified.releaseSequence,
                verified.payload,
                contractMajor,
            )
        } catch (error: Throwable) {
            throw ReferenceManifestStageException("manifest_release", error)
        }
    }

    override fun download(
        artifact: ReferenceReleaseArtifact,
        target: File,
        progress: (Long, Long) -> Unit,
    ) {
        require(artifact.contractMajor == contractMajor) {
            "reference artifact belongs to another contract"
        }
        target.parentFile?.let { parent ->
            check(parent.exists() || parent.mkdirs()) {
                "cannot create reference artifact directory"
            }
        }
        if (target.exists() && target.length() > artifact.sizeBytes) target.delete()
        if (target.isFile && target.length() == artifact.sizeBytes) {
            if (sha256(target) == artifact.sha256) {
                progress(artifact.sizeBytes, artifact.sizeBytes)
                return
            }
            check(target.delete()) { "cannot discard invalid complete reference checkpoint" }
        }

        var completed = target.takeIf { it.isFile }?.length() ?: 0L
        var digest = MessageDigest.getInstance("SHA-256")
        if (completed > 0) {
            target.inputStream().use { input ->
                val buffer = ByteArray(1024 * 1024)
                while (true) {
                    val read = input.read(buffer)
                    if (read < 0) break
                    if (read > 0) digest.update(buffer, 0, read)
                }
            }
            progress(completed, artifact.sizeBytes)
        }

        val connection = open(artifact.key)
        try {
            if (completed > 0) connection.setRequestProperty("Range", "bytes=$completed-")
            val response = connection.responseCode
            var append = completed > 0 && response == HttpsURLConnection.HTTP_PARTIAL
            if (completed > 0 && response == HttpsURLConnection.HTTP_OK) {
                append = false
                completed = 0
                digest = MessageDigest.getInstance("SHA-256")
            } else if (completed > 0 && response == HttpsURLConnection.HTTP_PARTIAL) {
                validateContentRange(
                    connection.getHeaderField("Content-Range"),
                    completed,
                    artifact.sizeBytes,
                )
            } else if (completed == 0L && response != HttpsURLConnection.HTTP_OK) {
                throw IllegalStateException("reference artifact HTTP status $response")
            } else if (completed > 0 && response != HttpsURLConnection.HTTP_PARTIAL) {
                throw IllegalStateException("reference artifact resume HTTP status $response")
            }

            val expectedResponseBytes = artifact.sizeBytes - completed
            val contentLength = connection.contentLengthLong
            if (contentLength >= 0) {
                require(contentLength == expectedResponseBytes) {
                    "reference artifact Content-Length mismatch"
                }
            }

            connection.inputStream.use { input ->
                FileOutputStream(target, append).use { output ->
                    val buffer = ByteArray(1024 * 1024)
                    while (true) {
                        val read = input.read(buffer)
                        if (read < 0) break
                        if (read == 0) continue
                        output.write(buffer, 0, read)
                        digest.update(buffer, 0, read)
                        completed += read
                        require(completed <= artifact.sizeBytes) {
                            "reference artifact exceeds signed size"
                        }
                        progress(completed, artifact.sizeBytes)
                    }
                    output.fd.sync()
                }
            }
        } finally {
            connection.disconnect()
        }

        if (completed != artifact.sizeBytes || digest.digest().toHex() != artifact.sha256) {
            target.delete()
            throw IllegalStateException(
                "reference artifact does not match signed size/SHA-256",
            )
        }
    }

    private fun fetchSmallText(key: String): String {
        val connection = open(key)
        try {
            connection.instanceFollowRedirects = false
            val response = connection.responseCode
            if (response != HttpsURLConnection.HTTP_OK) {
                throw ReferenceManifestHttpStatusException(response)
            }
            val contentLength = connection.contentLengthLong
            require(contentLength < 0 || contentLength <= MAX_MANIFEST_BYTES) {
                "reference manifest is too large"
            }
            val output = ByteArrayOutputStream()
            connection.inputStream.use { input ->
                val buffer = ByteArray(16 * 1024)
                while (true) {
                    val read = input.read(buffer)
                    if (read < 0) break
                    if (read == 0) continue
                    output.write(buffer, 0, read)
                    require(output.size() <= MAX_MANIFEST_BYTES) {
                        "reference manifest is too large"
                    }
                }
            }
            return output.toString(Charsets.UTF_8.name())
        } finally {
            connection.disconnect()
        }
    }

    private fun open(key: String): HttpsURLConnection {
        val ownPrefix = "reference/v2/contracts/$contractMajor/"
        require(
            (key == ReferenceReleaseProtocolV2.ROOT_KEY || key.startsWith(ownPrefix)) &&
                !key.contains("..") &&
                !key.startsWith("/"),
        ) { "invalid reference object key" }
        val resolved = baseUri.resolve(key)
        require(
            resolved.scheme == baseUri.scheme &&
                resolved.host == baseUri.host &&
                resolved.port == baseUri.port,
        ) { "reference object escaped configured origin" }
        return (resolved.toURL().openConnection() as HttpsURLConnection).apply {
            connectTimeout = CONNECT_TIMEOUT_MS
            readTimeout = READ_TIMEOUT_MS
            instanceFollowRedirects = false
            useCaches = false
            setRequestProperty("Accept-Encoding", "identity")
        }
    }

    private fun validateContentRange(value: String?, start: Long, total: Long) {
        val match = CONTENT_RANGE.matchEntire(value.orEmpty())
            ?: throw IllegalArgumentException("invalid reference artifact Content-Range")
        require(match.groupValues[1].toLong() == start) {
            "reference artifact resume offset mismatch"
        }
        require(match.groupValues[3].toLong() == total) {
            "reference artifact total size mismatch"
        }
        require(match.groupValues[2].toLong() == total - 1) {
            "reference artifact Content-Range must cover the remaining signed bytes"
        }
    }

    private fun sha256(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buffer = ByteArray(1024 * 1024)
            while (true) {
                val read = input.read(buffer)
                if (read < 0) break
                if (read > 0) digest.update(buffer, 0, read)
            }
        }
        return digest.digest().toHex()
    }

    private fun ByteArray.toHex(): String = joinToString("") { "%02x".format(it) }

    companion object {
        private const val CONNECT_TIMEOUT_MS = 15_000
        private const val READ_TIMEOUT_MS = 60_000
        private const val MAX_MANIFEST_BYTES = 1024 * 1024
        private val CONTENT_RANGE = Regex("bytes ([0-9]+)-([0-9]+)/([0-9]+)")
    }
}

class ReferenceManifestStageException(
    val stage: String,
    cause: Throwable,
) : IllegalStateException("reference manifest stage failed: $stage", cause)

class ReferenceManifestHttpStatusException(
    val statusCode: Int,
) : IllegalStateException("reference manifest HTTP status $statusCode")
