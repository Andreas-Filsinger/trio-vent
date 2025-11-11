#!/usr/bin/python
# -*- coding:utf-8 -*-
import sys
import os
import logging
import epd2in7_V2
import time
from PIL import Image,ImageDraw,ImageFont
import traceback
import gpiozero
import signal
from paho.mqtt import client as mqtt_client
import json5


# the "vent" device

device        = "trio-vent-106031846322"
topic_kueche  = "/command/vent-kueche"

# the GUI Device (it is me!)

local_device_id = "gui"

# Humidity Device

device_humidity      = "shellyhtg3-543204567354"
topic_humidity       = "/status/humidity:0"
#  sample Value = {"id": 0,"rh":60.3}


client = mqtt_client.Client(mqtt_client.CallbackAPIVersion.VERSION2, client_id=local_device_id)

KEY1 = 5
KEY2 = 6
KEY3 = 13
KEY4 = 19

kueche_vent = 0

def k1_pressed():
 global client
 global kueche_vent
 global client

 print("KEY1")
 if not client.is_connected():
  print(client.connect(mqtt_broker,1883),"Reconnect")
 if kueche_vent==0:
  print(client.publish(device + topic_kueche,"100"),"ON");
  kueche_vent = 100
 else:
  print(client.publish(device + topic_kueche,"0"),"OFF");
  kueche_vent = 0

def k2_pressed():
 print("KEY2")
def k3_pressed():
 print("KEY3")
def k4_pressed():
 print("KEY4")

def mqtt_event_message(client, userdata, message):

    # humidity message
    if message.topic==device_humidity+topic_humidity:

     payload = str(message.payload.decode("utf-8"))
     humidity = float(json5.loads(payload)["rh"])
     print(humidity, "%")


def mqtt_event_connect(client, userdata, connect_flags, reason_code, properties):
    print("Connected with result code " + str(reason_code))
    print(client.subscribe(device_humidity + topic_humidity, qos=1))

# 

k1 = gpiozero.Button(KEY1)
k1.when_pressed = k1_pressed
k2 = gpiozero.Button(KEY2)
k2.when_pressed = k2_pressed
k3 = gpiozero.Button(KEY3)
k3.when_pressed = k3_pressed
k4 = gpiozero.Button(KEY4)
k4.when_pressed = k4_pressed

# mqtt setup
mqtt_broker = "192.168.115.10"
mqtt_user   = "user"
mqtt_passwd = "user"

client.username_pw_set(mqtt_user, mqtt_passwd)
client.on_connect = mqtt_event_connect
client.on_message = mqtt_event_message
print(client.connect(mqtt_broker,1883),"Connect")


logging.basicConfig(level=logging.DEBUG)

try:

    logging.info("epd2in7 Demo")   
    epd = epd2in7_V2.EPD()
    
    '''2Gray(Black and white) display'''
    logging.info("init and Clear")
    epd.init()
#    epd.Clear()


    # set up the Fonts

    font_icon = ImageFont.truetype("MaterialIcons-Regular.ttf", 45)
    font_small = ImageFont.truetype("NotoSans-Bold.ttf", 18)
    font_normal = ImageFont.truetype("NotoSans-Bold.ttf", 25)
    font_huge = ImageFont.truetype("NotoSans-Bold.ttf", 40)
    
    # Drawing on the Horizontal image
    logging.info("4.Drawing on the Horizontal image...")

    Himage = Image.new('1', (epd.height, epd.width), 255)   # '1' a bilevel image 
                                                            # 255: clear the frame

    draw = ImageDraw.Draw(Himage)
    
    ICON_SETTINGS = '\ue8b8'
    ICON_KITCHEN  = '\ue7d3'
    ICON_AIR      = '\uefd8' # '\uf061'
    ICON_SHOWER   = '\uf061'
    ICON_FANOFF   = '\uec17'
    ICON_PLUS     = '\ueacf' # '\ue316'
    ICON_MINUS    = '\uead0' # '\ue313'
    ICON_ON       = '\ue3e7'
    ICON_OFF      = '\ue3e6'
    
    LINE_Y = 44
    draw.text((0, 0), ICON_KITCHEN+ICON_ON, font = font_icon, fill = 0)
    draw.text((0, LINE_Y), ICON_AIR+ICON_PLUS, font = font_icon, fill = 0)
    draw.text((0, LINE_Y*2), ICON_AIR+ICON_MINUS, font = font_icon, fill = 0)
    draw.text((0, LINE_Y*3), ICON_SHOWER+ICON_OFF, font = font_icon, fill = 0)

#    draw.text((10, 60), ICON_AIR, font = font_icon, fill = 0)
#    draw.text((10, 80), ICON_SHOWER, font = font_icon, fill = 0)
#    draw.text((10, 100), ICON_FANOFF, font = font_icon, fill = 0)

    # Fresh Air Power
    draw.text((130, LINE_Y*2), '38%', font = font_huge, fill = 0)
    # Humidity
    draw.text((130, LINE_Y*3), '26,2%', font = font_huge, fill = 0)

    epd.display_Base(epd.getbuffer(Himage))
    epd.sleep()

    # partial updates after "Base"-Image above
    while (True):

     print(".")
     client.loop()
    
        
except IOError as e:
    logging.info(e)
    
except KeyboardInterrupt:    
    logging.info("ctrl + c:")
    epd2in7_V2.epdconfig.module_exit(cleanup=True)
    exit()
