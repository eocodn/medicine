import org.gradle.api.artifacts.component.ModuleComponentIdentifier
import org.w3c.dom.Element
import javax.xml.parsers.DocumentBuilderFactory

gradle.projectsEvaluated {
    val app = gradle.rootProject.findProject(":app")
        ?: error("Android app project is unavailable")
    val lockedConfigurations = app.file("gradle.lockfile")
        .readLines()
        .asSequence()
        .filter { line -> line.isNotBlank() && !line.startsWith("#") && '=' in line }
        .flatMap { line -> line.substringAfter('=').split(',').asSequence() }
        .map(String::trim)
        .filter(String::isNotEmpty)
        .toSortedSet()

    app.tasks.register("prefetchLockedDependencies") {
        doLast {
            lockedConfigurations.forEach { name ->
                val configuration = app.configurations.findByName(name) ?: return@forEach
                if (!configuration.isCanBeResolved) return@forEach
                println("Prefetching $name")
                configuration.incoming.artifactView {
                    componentFilter { identifier -> identifier is ModuleComponentIdentifier }
                }.files.files.size
            }
        }
    }

    // AGP resolves the platform aapt2 binary through a detached configuration at
    // task execution time, so it is not represented by the app dependency lock.
    // Read the verified version instead of duplicating the AGP-coupled revision.
    val verificationMetadata = gradle.rootProject.file("gradle/verification-metadata.xml")
    val componentNodes = DocumentBuilderFactory.newInstance()
        .newDocumentBuilder()
        .parse(verificationMetadata)
        .getElementsByTagName("component")
    val aapt2Versions = (0 until componentNodes.length)
        .map { componentNodes.item(it) as Element }
        .filter { element ->
            element.getAttribute("group") == "com.android.tools.build" &&
                element.getAttribute("name") == "aapt2"
        }
        .map { element -> element.getAttribute("version") }
        .filter(String::isNotEmpty)
        .toSet()
    require(aapt2Versions.size == 1) {
        "Expected exactly one verified com.android.tools.build:aapt2 version, found $aapt2Versions"
    }
    val aapt2Version = aapt2Versions.single()
    val aapt2 = app.configurations.create("medicineAapt2Prefetch") {
        isCanBeConsumed = false
        isCanBeResolved = true
        resolutionStrategy.deactivateDependencyLocking()
    }
    app.dependencies.add(aapt2.name, "com.android.tools.build:aapt2:$aapt2Version:linux")
    app.tasks.register("prefetchMedicineAapt2") {
        doLast {
            aapt2.files.forEach { println("Prefetched ${it.name}") }
        }
    }
}