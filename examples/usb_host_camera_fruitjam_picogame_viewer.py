# SPDX-FileCopyrightText: Copyright (c) 2026 Tim Cocks for Adafruit Industries
#
# SPDX-License-Identifier: MIT
"""Show a USB webcam on the Fruit Jam's DVI output, drawn with picogame.
Press button 1 to save the current picture to /saves (the CPSAVES drive)."""

import time

import board
import displayio
import keypad
import picodvi
import picogame

import adafruit_usb_host_camera

WIDTH, HEIGHT = 320, 240

displayio.release_displays()
framebuffer = picodvi.Framebuffer(
    WIDTH,
    HEIGHT,
    clk_dp=board.CKP,
    clk_dn=board.CKN,
    red_dp=board.D0P,
    red_dn=board.D0N,
    green_dp=board.D1P,
    green_dn=board.D1N,
    blue_dp=board.D2P,
    blue_dn=board.D2N,
    color_depth=16,
)
target = picogame.Framebuffer(framebuffer, WIDTH, HEIGHT, native_rgb565=True)

buttons = keypad.Keys((board.BUTTON1,), value_when_pressed=False, pull=True)

camera = None
while camera is None:
    try:
        # Finds the camera among the attached devices (a keyboard, a mouse...)
        camera = adafruit_usb_host_camera.UVCCamera()
    except ValueError:
        time.sleep(1)

mode = camera.find_mode(WIDTH, HEIGHT)
camera.start(mode)

# The first access captures a frame and creates the bitmap.
bitmap = None
while bitmap is None:
    try:
        bitmap = camera.bitmap
    except RuntimeError:
        pass  # no complete frame in time; keep trying

# update_bitmap() decodes each frame into this same bitmap, and picogame draws
# that memory (no copy) straight into the DVI framebuffer.
sprite = picogame.Sprite(picogame.Bitmap(bitmap, bitmap.width, bitmap.height), x=0, y=0)
layers = [sprite]

photo = 0
while True:
    try:
        camera.update_bitmap()
    except RuntimeError:
        continue  # no complete frame in time; keep trying
    picogame.render(target, layers, None, 0, 0, WIDTH, HEIGHT)

    event = buttons.events.get()
    if event and event.pressed:
        path = f"/saves/photo_{photo}.jpg"
        camera.save_jpeg(path)
        print("Saved", path)
        photo += 1
