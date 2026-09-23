Introduction
============


.. image:: https://readthedocs.org/projects/adafruit-circuitpython-usb-host-camera/badge/?version=latest
    :target: https://docs.circuitpython.org/projects/usb_host_camera/en/latest/
    :alt: Documentation Status


.. image:: https://raw.githubusercontent.com/adafruit/Adafruit_CircuitPython_Bundle/main/badges/adafruit_discord.svg
    :target: https://adafru.it/discord
    :alt: Discord


.. image:: https://github.com/adafruit/Adafruit_CircuitPython_USB_Host_Camera/workflows/Build%20CI/badge.svg
    :target: https://github.com/adafruit/Adafruit_CircuitPython_USB_Host_Camera/actions
    :alt: Build Status


.. image:: https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json
    :target: https://github.com/astral-sh/ruff
    :alt: Code Style: Ruff

CircuitPython USB host driver for cameras


Dependencies
=============
This driver depends on:

* `Adafruit CircuitPython <https://github.com/adafruit/circuitpython>`_

Please ensure all dependencies are available on the CircuitPython filesystem.
This is easily achieved by downloading
`the Adafruit library and driver bundle <https://circuitpython.org/libraries>`_
or individual libraries can be installed using
`circup <https://github.com/adafruit/circup>`_.


`Adafruit Fruit Jam <https://www.adafruit.com/product/6200>`_

Installing from PyPI
=====================

On supported GNU/Linux systems like the Raspberry Pi, you can install the driver locally `from
PyPI <https://pypi.org/project/adafruit-circuitpython-usb-host-camera/>`_.
To install for current user:

.. code-block:: shell

    pip3 install adafruit-circuitpython-usb-host-camera

To install system-wide (this may be required in some cases):

.. code-block:: shell

    sudo pip3 install adafruit-circuitpython-usb-host-camera

To install in a virtual environment in your current project:

.. code-block:: shell

    mkdir project-name && cd project-name
    python3 -m venv .venv
    source .env/bin/activate
    pip3 install adafruit-circuitpython-usb-host-camera

Installing to a Connected CircuitPython Device with Circup
==========================================================

Make sure that you have ``circup`` installed in your Python environment.
Install it with the following command if necessary:

.. code-block:: shell

    pip3 install circup

With ``circup`` installed and your CircuitPython device connected use the
following command to install:

.. code-block:: shell

    circup install adafruit_usb_host_camera

Or the following command to update an existing version:

.. code-block:: shell

    circup update

Usage Example
=============

.. code-block:: python

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

Documentation
=============
API documentation for this library can be found on `Read the Docs <https://docs.circuitpython.org/projects/usb_host_camera/en/latest/>`_.

For information on building library documentation, please check out
`this guide <https://learn.adafruit.com/creating-and-sharing-a-circuitpython-library/sharing-our-docs-on-readthedocs#sphinx-5-1>`_.

Contributing
============

Contributions are welcome! Please read our `Code of Conduct
<https://github.com/adafruit/Adafruit_CircuitPython_USB_Host_Camera/blob/HEAD/CODE_OF_CONDUCT.md>`_
before contributing to help this project stay welcoming.
