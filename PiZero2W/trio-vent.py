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
   GPIO 18, 3,3 V 1 kHz
   GPIO 13, 3,3 V 1 kHz

 Raspberry Pi 5 4 Gbyte
 ======================
  User GUI

 Raspberry Pi 400
 ================
  Home Assistant 

'''
import os
import sys
import configparser
import pigpio
from paho.mqtt import client as mqtt_client
import json5
import time
import logging
from systemd import daemon
from systemd import journal

#
# To the mqtt Broker this program is like a Device, 
# subscribing-for and publish-this
#
local_device_id      = "trio-vent-106031846322" # software device
local_rev            = "183"                    # version / revision number

#
# A MQTT Broker is a 24/7 Message Router for Topics
# You can ask if you get notified if a interesting Topic changes
# The Broker is not persistent so values from "missed" messages can not be retrieved
# If a Client publish a value with a "retain"-Flag the Broker hold the value for a 
#  another Client after the moment of subscribe get a message about the stored value
#
mqtt_broker = "192.168.115.10"
mqtt_port   = 1883
mqtt_user   = "user"
mqtt_passwd = "user"

#  Shelly H&T Gen. 3 (für WC Luftfeuchtigkeit)
#
device_humidity      = "shellyhtg3-543204567354"
topic_humidity       = "/status/humidity:0"
#  sample Value = {"id": 0,"rh":60.3}


#  Shelly 1 Mini Gen4 "Sensor Toilettenlichtschalter" UND "Relais Küche Vent"
#
device_Shelly   = "shelly1minig4-ccba97c08c34"
#
topic_switch    = "/status/input:0"
#  sample Value = {"id":0,"state":false}
topic_relay    = "/command/switch:0"
#  sample Value = {"id":0, "source":"WS_in", "output":false,"temperature":{"tC":51.0, "tF":123.8}}
topic_ping     = "/command"
# possible Value = "announce"(fill announce) / "status_update"(send /status/*)

'''
 Persistent

  to ensure a smart Start-Up include the followings vars to persistence

  Humidity, WC_Light, 

'''

# Skizze
#
# (Vent-WV)     --- (M-Drehzahl-Steuergerät W) ----v
#                                                  |
# (Vent-Zuluft) --- (M-Drehzahl-Steuergerät Z) --- (Pi Zero 2 W) --mqtt--> 
#                                                  ^
#                                                  |
# Meanwell 5V   -----------------------------------+
#
# (Vent-Küche)  --- (Shelly 1 terminal "O") --mqtt-->
#
# (Schalter-Badlicht) --- (230V Relais) --- (Shelly 1 terminal "SW") --mqtt-->
#
# (Pi 5 Home Assistant) --mqtt--<
#  (ePaper-GUI)
#

# R
status_zuluft    = "/status/vent-zuluft"      # 0..100
status_kueche    = "/status/vent-kueche"      # true | false
status_wc        = "/status/vent-wc"          # 0..100
status_version   = "/status/rev"              # Software Version String
 
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

TIME_AUTO_ON           = 24*60*60 # [seconds] Forced ON after n seconds of idle

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
WC_Humidity         = float(0) # actual Humidity Value, 0=Unset
WC_Humidity_Age     = 0        # age of the Humidity Value, 0=Humidity Unset

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

#
# MQTT Icons to make clear "subscribe"(Heart) "message"(Arrow Down) "publish"(Arrow Up)
#
CHAR_UP   = "\u2191"
CHAR_DOWN = "\u2193"
CHAR_LIKE = "\u2665"
 
# PWM globals
PWM_FREQUENCY = 1000
PWM_PERCENT_FACTOR = 10000

# Zuluft PWM
PWM0_GPIO = 18

# WC Bad Abluft PWM
PWM1_GPIO = 13

# Logging Setup Systemd/Console
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
#
# are we running under systemd or console
#
if os.getenv("INVOCATION_ID")==None:
 # we run at console
 console_handler = logging.StreamHandler(sys.stdout)
 logger.addHandler(console_handler)
 logger.info("Log goes to console")
else:
 # we run as a systemd-service
 journald_handler = journal.JournalHandler(SYSLOG_IDENTIFIER="trio-vent")
 logger.addHandler(journald_handler)
 logger.info("Log goes to journalctl")

# Global Vars "pi" and "client"

pi = pigpio.pi()
client = mqtt_client.Client(local_device_id)
client.username_pw_set(mqtt_user, mqtt_passwd)

# local "sleep()" used in this world
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
 logger.info("MQTT" + CHAR_UP + " " + status_zuluft + " " + str(percent) + "% " + str( client.publish(local_device_id + status_zuluft, payload=percent, qos=1, retain=True)))
 
def pwm_vent_W(percent): 
 if percent>100:
  percent=100
 if percent<0:
  percent=0 
 pi.hardware_PWM(PWM1_GPIO, PWM_FREQUENCY, percent*PWM_PERCENT_FACTOR)
 logger.info("MQTT" + CHAR_UP + " " + status_wc + " " + str(percent) + "% " + str(client.publish(local_device_id + status_wc, payload=percent, qos=1, retain=True)))

def power_vent_K(onoff):
 if onoff:
  logger.info("MQTT" + CHAR_UP + " " + topic_relay + " on" + str(client.publish(device_Shelly + topic_relay, payload="on", qos=1))) 
  logger.info("MQTT" + CHAR_UP + " " + status_kueche + " true" + str(client.publish(local_device_id + status_kueche, payload="true", qos=1, retain=True)))
 else:
  logger.info("MQTT" + CHAR_UP + " " + topic_relay + " off" + str(client.publish(device_Shelly + topic_relay, payload="off", qos=1)))
  logger.info("MQTT" + CHAR_UP + " " + status_kueche + " false" + str(client.publish(local_device_id + status_kueche, payload="false", qos=1, retain=True)))

def mqtt_event_message(client, userdata, message):

    global KUECHE_Vent, WC_Light, time_OFF, WC_Humidity, WC_Humidity_Age, nachlauf

    # command "Küche Vent" {"true"|"false"}
    if message.topic == local_device_id + cmd_kueche:
     # vent
     v = message.payload.decode()
     logger.info(CHAR_DOWN + "MQTT Küche Vent " + v)
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
     logger.info(CHAR_DOWN + "MQTT Automatic " + v)
    
    # command "Zuluft Vent" {0..100}
    if message.topic == local_device_id + cmd_zuluft:
     v = int(message.payload.decode())
     logger.info(CHAR_DOWN + "MQTT ZULUFT Vent " + str(v))
     pwm_vent_Z(v)

    # command "WC Vent" {0..100} im Moment nur 0|60
    if message.topic == local_device_id + cmd_wc:
     v = int(message.payload.decode())
     logger.info(CHAR_DOWN + "MQTT WC Vent " + str(v))
     if v==0:
      nachlauf=1
     else: 
      time_OFF=-2
    
    # Status change "WC-Lichtschalter" {"true"|"false"}
    if message.topic == device_Shelly + topic_switch:
     s = json5.loads(message.payload.decode())["state"]
     logger.info(CHAR_DOWN + "MQTT WC-Licht " + str(s))
     if s:
      WC_Light = True
      time_OFF = 0
     if not s:
      WC_Light = False

    # Change of "Humidity" {JSON} 
    if message.topic == device_humidity + topic_humidity:
     payload = str(message.payload.decode("utf-8"))
     WC_Humidity = float(json5.loads(payload)["rh"])
     logger.info(CHAR_DOWN + "MQTT Humidity " + str(WC_Humidity) + "%")
     WC_Humidity_Age = 1
 
def mqtt_event_connect(client, userdata, flags, rc):
    logger.info("Connected with result code " + str(rc))

    # Remote Device Subscriptions
    logger.info("MQTT" + CHAR_LIKE + " " + topic_humidity + " " + str( client.subscribe(device_humidity + topic_humidity, qos=1)))
    logger.info("MQTT" + CHAR_LIKE + " " + topic_switch + " " + str( client.subscribe(device_Shelly + topic_switch, qos=1)))

    # Own Subscriptions for me to serve
    logger.info("MQTT" + CHAR_LIKE + " " + cmd_automatic + " " + str( client.subscribe(local_device_id + cmd_automatic, qos=1)))
    logger.info("MQTT" + CHAR_LIKE + " " + cmd_zuluft + " " + str( client.subscribe(local_device_id + cmd_zuluft, qos=1)))
    logger.info("MQTT" + CHAR_LIKE + " " + cmd_kueche + " " + str( client.subscribe(local_device_id + cmd_kueche, qos=1)))
    logger.info("MQTT" + CHAR_LIKE + " " + cmd_wc + " " + str( client.subscribe(local_device_id + cmd_wc, qos=1)))

def mqtt_event_disconnect(client, userdata, rc):
    logger.info("MQTT event disconnect, giving up")
    exit(1)

# Connect to the MQTT Server

logger.info("MQTT connect ...")
client.on_connect = mqtt_event_connect
client.on_message = mqtt_event_message
client.on_disconnect = mqtt_event_disconnect
client.connect(mqtt_broker, mqtt_port, keepalive=20)
trio_vent_sleep(3)

# Vent Motor Speed up

logger.info("Motor Init ...")
pwm_vent_Z(100)
pwm_vent_W(100)
trio_vent_sleep(6)

# Vent initial Setup

pwm_vent_Z(ZULUFT_Vent_Percent)
pwm_vent_W(0)

last_log   = ""
actual_log = ""

daemon.notify('READY=1')
client.publish(local_device_id + status_version, local_rev, qos=1, retain=True)
logger.info("trio-vent Rev. " + local_rev + " startup complete")

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
   
  if WC_Humidity_Age>0:
   WC_Humidity_Age += 1

  # Debug all Parameters, "OFF" is to noisy
  actual_log = ( " ON=" + str(time_ON) + 
   " LIGHT=" + str(time_LIGHT) + 
   " NL=" + str(nachlauf) +
   " L=" + str(WC_Light) + 
   " V=" + str(WC_Vent) +
   " H=" + str(WC_Humidity) +
   " K=" + str(KUECHE_Vent)
  )

  if actual_log!=last_log:
   logger.info("OFF=" + str(time_OFF) + actual_log)
   last_log = actual_log

  if time_OFF==-1:
   # Zwangsbelüftung nach langem Stillstand
   logger.info("Vent on after Idle")
   pwm_vent_W(100)
   time.sleep(4)
   pwm_vent_W(WC_Vent_Silent)
   WC_Vent = True
   nachlauf = cfg_nachlauf_lang
     
  if time_LIGHT==cfg_anlaufverzoegerung:
   # Belüftung nun an
   if not WC_Vent:
    logger.info("Vent on after Light on")
    pwm_vent_W(100)
    time.sleep(4)
    pwm_vent_W(WC_Vent_Normal)
    pwm_vent_Z(ZULUFT_Vent_Percent+3)
    WC_Vent = True

   # Nachlauf setzen (aber nicht rücksetzen)
   nachlauf = max(cfg_nachlauf_kurz, nachlauf)
   
  if (WC_Humidity>=cfg_entfeuchtung_an) and not WC_Vent:
   logger.info("Vent on after high Humidity")
   pwm_vent_W(100)
   time.sleep(4)
   pwm_vent_W(WC_Vent_Silent)   
   WC_Vent = True
   # Nachlauf setzen (aber nicht rücksetzen)
   nachlauf = max(cfg_nachlauf_duschen, nachlauf)
   
  if time_LIGHT==cfg_anlaufverzoegerung+cfg_kleinesgeschaeft:
   # Belüftung nun länger laufen lassen
   logger.info("a bit longer Stay")
   nachlauf = max(cfg_nachlauf_lang, nachlauf)

  if time_LIGHT==cfg_anlaufverzoegerung+cfg_duschen:
   # Belüftung nun ganz lang laufen lassen
   logger.info("a full long Stay")
   nachlauf = max(cfg_nachlauf_duschen, nachlauf)
  
  if nachlauf==0:
   logger.info("WC vent OFF")
   # Switch off WC Vent
   pwm_vent_W(0)   
   # Zuluft wieder normal
   pwm_vent_Z(ZULUFT_Vent_Percent)
   WC_Vent = False
   time_ON = 0
   time_OFF = -TIME_AUTO_ON
   nachlauf = -1
   
  if WC_Humidity_Age>60*50: # = 50 [Min]
   logger.info("no humidity-value refresh, unset old value to 0")
   WC_Humidity_Age = 0
   WC_Humidity = float(0)
   
  daemon.notify("WATCHDOG=1") 
  trio_vent_sleep(1)
  if not client.is_connected():
   logger.info("MQTT is not connected, giving up")
   exit(1)
     