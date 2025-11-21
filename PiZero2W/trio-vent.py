#!/usr/bin/python
# -*- coding:utf-8 -*-
'''

 trio-vent  (c) 2025 Andreas Filsinger, GPL-Licence
 =========

 Control two Vent Motors by Hardware PWM
 Send the vent-Status (0..100) to MQTT Broker via "sensor/#"
 Read Humidity from a Shelly Temp & Humidity Device via MQTT
 Read Light-Switch Status ("SW") from a Shelly 1 via MQTT
 Write Relay 0/1 ("O") to a Shelly 1 via MQTT

 Logik:

  addiere Volumen von "Kueche" zu "Zuluft" damit kein Unterdruck entsteht
  addiere Volumen von "Bad" zu "Zuluft" damit kein Unterdruck ensteht
  fahre Bad auch hoch wenn Kueche hochfährt damit nicht aus dem Bad Luft gezogen wird
  fahre Bad hoch wenn Luftfeuchtigkeit >69%

 KEMO M240
 =========
  PWM Spannung: 3 V bis 24 V
  PWM Frequenz: 100 Hz bis 10 kHz

 Raspberry Pi Zero 2 W
 =====================
  pwmchip0
   Pin 18, 3,3 V 1 kHz
   Pin 13, 3,3 V 1 kHz

 Raspberry Pi 5 4 Gbyte
 ======================
  User GUI

 Raspberry Pi 400
 ================
  Home Assistant 

'''

import configparser
import pigpio
from paho.mqtt import client as mqtt_client
import json5
import time

CHAR_UP   = "\u2191"
CHAR_DOWN = "\u2193"
CHAR_LIKE = "\u2665"


#
# To the mqtt Broker this program is like a Device, 
# subscribing-for and publish-this
#
local_device_id      = "trio-vent-106031846322"

#
# A MQTT Broker is a 24/7 Message Router for Topics
# You can ask if you get notified if a interesting Topic changes
# The Broker is not persistent so values from "missed" messages can not be retrieved
#
mqtt_broker = "192.168.115.10"
mqtt_user   = "user"
mqtt_passwd = "user"

# Remote Devices

#  Shelly-Feuchtigkeit auslesen

device_humidity      = "shellyhtg3-543204567354"

topic_humidity       = "/status/humidity:0"
#  sample Value = {"id": 0,"rh":60.3}


#  Shelly 1 Mini Gen4 "A" "Sensor Toilettenlichtschalter" + "Schalter Küche Vent"

device_Shelly   = "shelly1minig4-ccba97c08c34"

topic_switch    = "/status/input:0"
# sample Value = {"id":0,"state":false}

topic_relay    = "/command/switch:0"
# sample Value = {"id":0, "source":"WS_in", "output":false,"temperature":{"tC":51.0, "tF":123.8}}


# Blackbox Devices

#  Motordrehzahl Steuergerät PWM0 "Zuluft-Motor"
#  Motordrehzahl Steuergerät PWM1 "Bad-Motor"  

'''
 Persistent

  to ensure a smart Start-Up include the followings vars to persistence

  Humidity, WC_Light, 

'''


# Skizze
#
# (M-Bad)    --- (M-Drehzahl-Steuergerät B) ----v
#                                               |
# (M-Zuluft) --- (M-Drehzahl-Steuergerät A) --- (Pi Zero 2 W) --mqtt--> 
#
# (M-Küche)  --- (Shelly 1 "O") --mqtt-->
#
# (Schalter-Badlicht) --- (Shelly 1 "SW") --mqtt-->
#
# (Pi 5 Home Assistant) --mqtt--<
#  (ePaper-GUI)
#

# R
status_zuluft    = "/status/vent-zuluft"      # 0..100
status_kueche    = "/status/vent-kueche"      # true | false
status_wc        = "/status/vent-wc"          # 0..100
 
# R/W
cmd_automatic = "/command/automatic"        # true | false
cmd_zuluft    = "/command/vent-zuluft"      # 0..100
cmd_kueche    = "/command/vent-kueche"      # true | false
cmd_wc        = "/command/vent-wc"          # 0..100

# Vent Wartezeiten
cfg_anlaufverzoegerung = 15      # [seconds]
cfg_kleinesgeschaeft   = 60      # [seconds]
cfg_duschen            = 60*3    # [seconds]

# Vent Laufzeiten
cfg_nachlauf_kurz      = 50      # [seconds]
cfg_nachlauf_lang      = 3*60    # [seconds]
cfg_nachlauf_duschen   = 5*60    # [seconds]

# Humidity Grenzen
cfg_entfeuchtung_an    = 70       # [%], "0" = unknown

# Persistent

TIME_AUTO_ON           = 24*60*60 # [seconds] Forced ON after n Seconds

#
# time_OFF, ein Zeitzähler [Sekunden] von -TIME_AUTO_ON .. 0 .. cfg_nachlauf_lang
# 
# -x .. -1                      wartet auf Auto-ON
# 0 .. cfg_anlaufverzoegerung   verzögert den Anlauf
# 
#
time_OFF            = -TIME_AUTO_ON

#
# time_ON, ein Zeitzähler [Sekunden] der misst, wielange der Ventilator schon AN ist
#
time_ON             = 0

#
# time_LIGHT, ein Zeitzähler [Sekunden] der misst, wielange das Licht schon AN ist
#
time_LIGHT          = 0

#
# WC
#
WC_Light            = False    # False=Light off 
WC_Vent             = False    # False=Vent stopped

WC_Vent_Normal      = 31       # % Speed of Vent
WC_Vent_Silent      = 23       # % Speed of Vent
WC_Humidity         = float(0) # actual Humidity Value

#
# KÜCHE
#
KUECHE_Vent         = False

#
# ZULUFT
#
ZULUFT_Vent_Percent = 25 

#
# Wie lange soll der Lüfter noch nachlaufen
#
nachlauf           = -1
 
# R
sensor_kueche      = "/sensor/volumen-kueche"  # 0|1
sensor_bad         = "/sensor/volumen-bad"     # 0..100
sensor_zuluft      = "/sensor/volumen-zuluft"  # 0..100
sensor_schalter    = "/sensor/schalter"        # 0|1
sensor_luftfeuchte = "/sensor/luftfeuchte"     # 0..100

# Zuluft
PWM0_GPIO = 18

# Bad Abluft
PWM1_GPIO = 13
PWM_FREQUENCY = 1000
PWM_PERCENT_FACTOR = 10000

# Global Vars "pi" and "client"

pi = pigpio.pi()
client = mqtt_client.Client(local_device_id)
client.username_pw_set(mqtt_user, mqtt_passwd)

# sleep() used in this world
def trio_vent_sleep(seconds):
 while seconds>0:
  client.loop()
  time.sleep(1)
  seconds -= 1

def pwm_vent_Z(percent):
 if percent>100:
  percent=100
 if percent<0:
  percent=0 
 pi.hardware_PWM(PWM0_GPIO, PWM_FREQUENCY, percent*PWM_PERCENT_FACTOR)
 print("MQTT" + CHAR_UP, status_zuluft, "Vent Z", percent, "%", client.publish(local_device_id + status_zuluft, payload=percent, qos=1))
 
def pwm_vent_W(percent): 
 if percent>100:
  percent=100
 if percent<0:
  percent=0 
 pi.hardware_PWM(PWM1_GPIO, PWM_FREQUENCY, percent*PWM_PERCENT_FACTOR)
 print("MQTT" + CHAR_UP, status_wc, "Vent W", percent, "%", client.publish(local_device_id + status_wc, payload=percent, qos=1))

def power_vent_K(onoff):
 if onoff:
  print("MQTT" + CHAR_UP, topic_relay, "on", client.publish(device_Shelly + topic_relay, payload="on", qos=1)) 
  print("MQTT" + CHAR_UP, status_kueche, "true", client.publish(local_device_id + status_kueche, payload="true", qos=1))
 else:
  print("MQTT" + CHAR_UP, topic_relay, "off", client.publish(device_Shelly + topic_relay, payload="off", qos=1))
  print("MQTT" + CHAR_UP, status_kueche, "false", client.publish(local_device_id + status_kueche, payload="false", qos=1))

def mqtt_event_message(client, userdata, message):

    global KUECHE_Vent, WC_Light, time_OFF, WC_Humidity, nachlauf

    # command "Küche Vent" {"true"|"false"}
    if message.topic == local_device_id + cmd_kueche:
     # vent
     v = message.payload.decode()
     print(CHAR_DOWN + "MQTT Küche Vent", v)
     if v=="true":
      pwm_vent_Z(100)
      KUECHE_Vent = True
     else:
      pwm_vent_Z(ZULUFT_Vent_Percent)
      KUECHE_Vent = False
     power_vent_K(KUECHE_Vent)
     
    # command "Automatic" {"true"|"false"}
    if message.topic == local_device_id + cmd_automatic:
     v = message.payload.decode()
     print(CHAR_DOWN + "MQTT Automatic", v)
    
    # command "Zuluft Vent" {0..100}
    if message.topic == local_device_id + cmd_zuluft:
     v = int(message.payload.decode())
     print(CHAR_DOWN + "MQTT ZULUFT Vent", v)
     pwm_vent_Z(v)

    # command "WC Vent" {0..100} im Moment nur 0|60
    if message.topic == local_device_id + cmd_wc:
     v = int(message.payload.decode())
     print(CHAR_DOWN + "MQTT WC Vent", v)
     if v==0:
      nachlauf=1
     else: 
      time_OFF=-2
    
    # Status change "WC-Lichtschalter" {"true"|"false"}
    if message.topic == device_Shelly + topic_switch:
     s = json5.loads(message.payload.decode())["state"]
     print(CHAR_DOWN + "MQTT WC-Licht ","´",s,"´",sep="")
     if s:
      WC_Light = True
      time_OFF = 0
     if not s:
      WC_Light = False

    # Change of "Humidity" {JSON} 
    if message.topic == device_humidity+topic_humidity:
     payload = str(message.payload.decode("utf-8"))
     WC_Humidity = float(json5.loads(payload)["rh"])
     print(CHAR_DOWN + "MQTT Humidity", WC_Humidity, "%")
     
 
def mqtt_event_connect(client, userdata, flags, rc):
    print("Connected with result code " + str(rc))

    # Remote Device Subscriptions
    print("MQTT" + CHAR_LIKE, topic_humidity, client.subscribe(device_humidity + topic_humidity, qos=1))
    print("MQTT" + CHAR_LIKE, topic_switch, client.subscribe(device_Shelly + topic_switch, qos=1))

    # Own Subscriptions for me to serve
    print("MQTT" + CHAR_LIKE, cmd_automatic, client.subscribe(local_device_id + cmd_automatic, qos=1))
    print("MQTT" + CHAR_LIKE, cmd_zuluft, client.subscribe(local_device_id + cmd_zuluft, qos=1))
    print("MQTT" + CHAR_LIKE, cmd_kueche, client.subscribe(local_device_id + cmd_kueche, qos=1))
    print("MQTT" + CHAR_LIKE, cmd_wc, client.subscribe(local_device_id + cmd_wc, qos=1))

# Connect to the MQTT Server

print("MQTT connect ...")
client.on_connect = mqtt_event_connect
client.on_message = mqtt_event_message
print(client.connect(mqtt_broker, 1883))
trio_vent_sleep(3)

# Vent Motor Speed up

print ("Motor Init ...")
pwm_vent_Z(100)
pwm_vent_W(100)
trio_vent_sleep(6)

# Vent initial Setup

pwm_vent_Z(ZULUFT_Vent_Percent)
pwm_vent_W(0)

while True:

  # Clocks
  time_OFF += 1

  if WC_Vent:
   time_ON += 1

  if WC_Light: 
   time_LIGHT += 1
  else:
   if (time_LIGHT>0) and (time_LIGHT<=cfg_anlaufverzoegerung):
    time_LIGHT -= 1
   else:
    time_LIGHT = 0
    
  if WC_Vent and not WC_Light:
   nachlauf -= 1

  # Debug all Parameters  
  print(
  "OFF", time_OFF, 
  " ON", time_ON, 
  " LIGHT", time_LIGHT, 
  " NL", nachlauf,
  " | ",
  " L", WC_Light, 
  " V", WC_Vent,
  " H", WC_Humidity,
  " K", KUECHE_Vent,
  sep="")      
  
  if time_OFF==-1:
   # Zwangsbelüftung nach langem Stillstand
   print("Vent on after Idle")
   pwm_vent_W(100)
   time.sleep(4)
   pwm_vent_W(WC_Vent_Silent)
   WC_Vent = True
   nachlauf = cfg_nachlauf_lang
     
  if time_LIGHT==cfg_anlaufverzoegerung:
   # Belüftung nun an
   if not WC_Vent:
    print("Vent on after Light on")
    pwm_vent_W(100)
    time.sleep(4)
    pwm_vent_W(WC_Vent_Normal)
    pwm_vent_Z(ZULUFT_Vent_Percent+3)
    WC_Vent = True

   # Nachlauf setzen (aber nicht rücksetzen)
   nachlauf = max(cfg_nachlauf_kurz, nachlauf)
   
  if (WC_Humidity>=cfg_entfeuchtung_an) and not WC_Vent:
   print("Vent on after high Humidity")
   pwm_vent_W(100)
   time.sleep(4)
   pwm_vent_W(WC_Vent_Silent)   
   WC_Vent = True
   # Nachlauf setzen (aber nicht rücksetzen)
   nachlauf = max(cfg_nachlauf_duschen, nachlauf)
   
  if time_LIGHT==cfg_anlaufverzoegerung+cfg_kleinesgeschaeft:
   # Belüftung nun länger laufen lassen
   print("a bit longer Stay")
   nachlauf = max(cfg_nachlauf_lang, nachlauf)

  if time_LIGHT==cfg_anlaufverzoegerung+cfg_duschen:
   # Belüftung nun ganz lang laufen lassen
   print("a full long Stay")
   nachlauf = max(cfg_nachlauf_duschen, nachlauf)
  
  if nachlauf==0:
   # Belüftung wieder aus
   print("Vent OFF")
   # Switch off WC Vent
   pwm_vent_W(0)   
   pwm_vent_Z(ZULUFT_Vent_Percent)
   WC_Vent = False
   time_ON = 0
   time_OFF = -TIME_AUTO_ON
   nachlauf = -1
   
  trio_vent_sleep(1)
  