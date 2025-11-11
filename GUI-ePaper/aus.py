#!/usr/bin/python
# -*- coding:utf-8 -*-
import sys
import os

picdir = "/root/e-Paper/RaspberryPi_JetsonNano/python/pic/"

#picdir = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), 'pic')

#libdir = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), 'lib')
#if os.path.exists(libdir):
#    sys.path.append(libdir)

import logging
import epd2in7_V2
import time
from PIL import Image,ImageDraw,ImageFont
import traceback

#logging.basicConfig(level=logging.DEBUG)


epd = epd2in7_V2.EPD()
epd.init()
epd.Clear()
epd.sleep()

