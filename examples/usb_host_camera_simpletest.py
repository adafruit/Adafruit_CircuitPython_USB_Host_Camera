# SPDX-FileCopyrightText: Copyright (c) 2026 Tim Cocks for Adafruit Industries
#
# SPDX-License-Identifier: MIT
"""Take a picture with a USB webcam and save it as a JPEG.

The file goes to /saves, which shows up on your computer as the CPSAVES drive.
"""

import time

import adafruit_usb_host_camera

camera = None
while camera is None:
    try:
        # Finds the camera among the attached devices (a keyboard, a mouse...)
        camera = adafruit_usb_host_camera.UVCCamera()
    except ValueError:
        print("Plug a USB camera into the USB host port")
        time.sleep(2)

print("Camera modes:", camera.modes)
mode = camera.find_mode(640, 480)
print("Using", mode)
camera.start(mode)
frame = camera.capture()
camera.stop()

camera.save_jpeg("/saves/photo.jpg", frame)
print("Saved /saves/photo.jpg,", len(frame), "bytes")
