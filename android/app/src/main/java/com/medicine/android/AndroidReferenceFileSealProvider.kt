package com.medicine.android

import android.system.Os
import android.system.OsConstants
import java.io.File

class AndroidReferenceFileSealProvider : ReferenceFileSealProvider {
    override fun capture(file: File): ReferenceFileSeal? {
        if (!file.isFile) return null
        val stat = Os.stat(file.absolutePath)
        val writableMask = OsConstants.S_IWUSR or OsConstants.S_IWGRP or OsConstants.S_IWOTH
        return ReferenceFileSeal(
            sizeBytes = stat.st_size,
            modifiedMarker = stat.st_mtime,
            changedMarker = stat.st_ctime,
            identityKey = "${stat.st_dev}:${stat.st_ino}",
            writable = stat.st_mode and writableMask != 0,
        )
    }
}