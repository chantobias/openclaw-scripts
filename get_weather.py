#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""天氣查詢腳本 - 眼鏡妹"""

import urllib.request
import json

def get_weather(city="香港"):
    """獲取天氣資訊"""
    url = f"https://wttr.in/{city}?format=j1"
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            data = json.loads(response.read().decode())
            current = data["current_condition"][0]
            print(f"🌤 {city} 天氣")
            print(f"溫度: {current['temp_C']}°C")
            print(f"濕度: {current['humidity']}%")
            print(f"天氣: {current['weatherDesc'][0]['value']}")
    except Exception as e:
        print(f"錯誤: {e}")

if __name__ == "__main__":
    get_weather()
