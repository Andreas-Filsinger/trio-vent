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

CHAR_UP   = "\u2191"
CHAR_DOWN = "\u2193"
CHAR_LIKE = "\u2665"

ICON_SETTINGS = '\ue8b8'
ICON_KITCHEN  = '\ue7d3'
ICON_AIR      = '\uefd8' # '\uf061'
ICON_SHOWER   = '\uf061'
ICON_FANOFF   = '\uec17'
ICON_PLUS     = '\ueacf' # '\ue316'
ICON_MINUS    = '\uead0' # '\ue313'
ICON_ON       = '\ue3e7'
ICON_OFF      = '\ue3e6'

# the "vent" device

device        = "trio-vent-106031846322"
# R
status_zuluft    = "/status/vent-zuluft"      # 0..100
status_kueche    = "/status/vent-kueche"      # true | false
status_wc        = "/status/vent-wc"          # 0..100

# R/W
cmd_automatic = "/command/automatic"        # true | false
cmd_zuluft    = "/command/vent-zuluft"      # 0..100
cmd_kueche    = "/command/vent-kueche"      # true | false
cmd_wc        = "/command/vent-wc"          # 0..100


# the GUI Device (it is me!)

local_device_id = "gui"

# Humidity Device

device_humidity      = "shellyhtg3-543204567354"
topic_humidity       = "/status/humidity:0"
#  sample Value = {"id": 0,"rh":60.3}

humidity    = 0.0
WC_Vent     = 0
ZULUFT_Vent = 0

client = mqtt_client.Client(mqtt_client.CallbackAPIVersion.VERSION2, client_id=local_device_id)

KEY1 = 5
KEY2 = 6
KEY3 = 13
KEY4 = 19

kueche_vent = False
canvas_need_update = False

def k1_pressed():
 global kueche_vent
 print("KEY1")
 if kueche_vent:
  print("MQTT" + CHAR_UP, cmd_kueche, client.publish(device + cmd_kueche,"false"),"OFF")
  kueche_vent = False
 else:
  print("MQTT" + CHAR_UP, cmd_kueche, client.publish(device + cmd_kueche,"true"),"ON")
  kueche_vent = True

def k2_pressed():
 global ZULUFT_Vent
 print("KEY2")
 # more Power on ZULUFT_Vent
 match ZULUFT_Vent:
  case 25:
   ZULUFT_Vent=27
  case 27:
   ZULUFT_Vent=30
  case 30:
   ZULUFT_Vent=35
  case 35:
   ZULUFT_Vent=40
  case 0:
   ZULUFT_Vent=25
 print("MQTT" + CHAR_UP, cmd_zuluft, ZULUFT_Vent, client.publish(device + cmd_zuluft, payload=ZULUFT_Vent, qos=1))
 
def k3_pressed():
 global ZULUFT_Vent
 print("KEY3")
 # less Power on ZULUFT_Vent
 match ZULUFT_Vent:
  case 27:
   ZULUFT_Vent=25
  case 30:
   ZULUFT_Vent=25
  case 35:
   ZULUFT_Vent=30
  case 40:
   ZULUFT_Vent=35
  case 0:
   ZULUFT_Vent=40
 print("MQTT" + CHAR_UP, cmd_zuluft, ZULUFT_Vent, client.publish(device + cmd_zuluft, payload=ZULUFT_Vent, qos=1))
 
def k4_pressed():
 print("KEY4")
 # Power the WC_Vent for a few minutes
 if WC_Vent==0:
  print("MQTT" + CHAR_UP, cmd_wc, "60", client.publish(device + cmd_wc, payload=60, qos=1))
 else:
  print("MQTT" + CHAR_UP, cmd_wc, "0", client.publish(device + cmd_wc, payload=0, qos=1))
  
def mqtt_event_message(client, userdata, message):
   
    global canvas_need_update, humidity, WC_Vent, ZULUFT_Vent

    # humidity message
    if message.topic == device_humidity + topic_humidity:
     payload = str(message.payload.decode("utf-8"))
     humidity = float(json5.loads(payload)["rh"])
     print(CHAR_DOWN + "MQTT", topic_humidity, humidity, "%")
     canvas_need_update = True
     
    # WC Vent message
    if message.topic == device + status_wc:
     WC_Vent = int(message.payload.decode())
     print(CHAR_DOWN + "MQTT", status_wc, WC_Vent)
     canvas_need_update = True
     
    # ZULUFT Vent message
    if message.topic == device + status_zuluft:
     ZULUFT_Vent = int(message.payload.decode())
     print(CHAR_DOWN + "MQTT", status_zuluft, ZULUFT_Vent)
     canvas_need_update = True

def mqtt_event_connect(client, userdata, connect_flags, reason_code, properties):
    print("Connected with result code " + str(reason_code))
    print("MQTT" + CHAR_LIKE, topic_humidity, client.subscribe(device_humidity + topic_humidity, qos=1))
    print("MQTT" + CHAR_LIKE, status_wc,  client.subscribe(device + status_wc, qos=1))
    print("MQTT" + CHAR_LIKE, status_zuluft,  client.subscribe(device + status_zuluft, qos=1))


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

font_icon = ImageFont.truetype("MaterialIcons-Regular.ttf", 45)
font_small = ImageFont.truetype("NotoSans-Bold.ttf", 18)
font_normal = ImageFont.truetype("NotoSans-Bold.ttf", 25)
font_huge = ImageFont.truetype("NotoSans-Bold.ttf", 30)

logging.basicConfig(level=logging.DEBUG)

epd = epd2in7_V2.EPD()


def draw_canvas():

    global canvas_need_update
    canvas_need_update = False
    
    epd.init()
#    epd.Clear()
    Himage = Image.new('1', (epd.height, epd.width), 255)   # '1' a bilevel image 
                                                           # 255: clear the frame
    draw = ImageDraw.Draw(Himage)
    
    LINE_Y = 44
    if kueche_vent:
     draw.text((0, 0), ICON_KITCHEN+ICON_ON, font = font_icon, fill = 0)
    else: 
     draw.text((0, 0), ICON_KITCHEN+ICON_OFF, font = font_icon, fill = 0)
    draw.text((0, LINE_Y), ICON_AIR+ICON_PLUS, font = font_icon, fill = 0)
    draw.text((0, LINE_Y*2), ICON_AIR+ICON_MINUS, font = font_icon, fill = 0)
    if WC_Vent==0: 
     draw.text((0, LINE_Y*3), ICON_SHOWER+ICON_OFF, font = font_icon, fill = 0)
    else: 
     draw.text((0, LINE_Y*3), ICON_SHOWER+ICON_ON, font = font_icon, fill = 0)

#    draw.text((10, 60), ICON_AIR, font = font_icon, fill = 0)
#    draw.text((10, 80), ICON_SHOWER, font = font_icon, fill = 0)
#    draw.text((10, 100), ICON_FANOFF, font = font_icon, fill = 0)

    # Fresh Air Power
    draw.text((130, LINE_Y*2), str(ZULUFT_Vent), font = font_huge, fill = 0)
    # Humidity
    draw.text((130, LINE_Y*3), "{:.1f}".format(humidity)+"%", font = font_huge, fill = 0)

    epd.display_Base(epd.getbuffer(Himage))
    epd.sleep()
    

try:

    canvas_need_update = True
    while (True):

     client.loop()
     if canvas_need_update:
      draw_canvas()
     else:
      print(".") 
      time.sleep(1)
        
except IOError as e:
    logging.info(e)
    
except KeyboardInterrupt:    
    logging.info("ctrl + c:")
    epd2in7_V2.epdconfig.module_exit(cleanup=True)
    exit()
