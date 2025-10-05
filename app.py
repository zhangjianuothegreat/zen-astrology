import os
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from kerykeion import AstrologicalSubject, KerykeionChartSVG
import json
import logging
import pytz
from datetime import datetime

app = Flask(__name__)
CORS(app)

# 设置日志
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# 加载解读模板
try:
    with open('interpretations.json', 'r', encoding='utf-8') as f:
        interpretations = json.load(f)
except FileNotFoundError:
    logger.error("interpretations.json not found. Please create it in the project directory.")
    interpretations = {"planets_in_houses": {}, "aspects": {}}
except json.JSONDecodeError as e:
    logger.error(f"Failed to parse interpretations.json: {e}")
    interpretations = {"planets_in_houses": {}, "aspects": {}}

# 加载城市数据
try:
    with open('city_data.json', 'r', encoding='utf-8') as f:
        city_data = json.load(f)
except FileNotFoundError:
    logger.error("city_data.json not found. Please create it in the project directory.")
    city_data = {}
except json.JSONDecodeError as e:
    logger.error(f"Failed to parse city_data.json: {e}")
    city_data = {}

# UTC 偏移到 pytz 时区的映射
utc_to_pytz = {
    # 7 国常用时区（城市选择模式）
    '-10': 'Pacific/Honolulu',  # US: Hawaii
    '-9': 'America/Anchorage',  # US: Alaska
    '-8': 'America/Los_Angeles',  # US/CA: Pacific, MX: Tijuana
    '-7': 'America/Denver',  # US/CA: Mountain, MX: Chihuahua
    '-6': 'America/Chicago',  # US/CA: Central, MX: Mexico City
    '-5': 'America/New_York',  # US/CA: Eastern, MX: Cancun
    '-4': 'America/Halifax',  # CA: Atlantic
    '0': 'Europe/London',  # GB: London
    '2': 'Africa/Johannesburg',  # ZA: Johannesburg
    '8': 'Australia/Perth',  # AU: Perth
    '9.5': 'Australia/Adelaide',  # AU: Adelaide
    '10': 'Australia/Sydney',  # AU: Sydney
    '12': 'Pacific/Auckland',  # NZ: Auckland
    '12.75': 'Pacific/Chatham',  # NZ: Chatham
    # 手动输入模式的额外时区
    '-12': 'Etc/GMT+12',  # Baker Island
    '-11': 'Pacific/Pago_Pago',  # American Samoa
    '-3': 'America/Sao_Paulo',  # Brasilia
    '-2': 'Atlantic/South_Georgia',  # Mid-Atlantic
    '-1': 'Atlantic/Azores',  # Azores
    '1': 'Europe/Paris',  # Paris
    '3': 'Europe/Moscow',  # Moscow
    '4': 'Asia/Dubai',  # Dubai
    '5': 'Asia/Karachi',  # Karachi
    '5.5': 'Asia/Kolkata',  # India
    '6': 'Asia/Dhaka',  # Dhaka
    '7': 'Asia/Bangkok',  # Bangkok
    '8': 'Asia/Shanghai',  # Beijing
    '9': 'Asia/Tokyo',  # Tokyo
    '11': 'Pacific/Guadalcanal',  # Solomon Islands
    '13': 'Pacific/Tongatapu',  # Tonga
    '14': 'Pacific/Kiritimati'  # Line Islands
}

# 获取城市数据路由
@app.route('/get-cities', methods=['GET'])
def get_cities():
    return jsonify(city_data)

# 主页路由
@app.route('/')
@app.route('/index.html')
def index():
    return render_template('index.html')

@app.route('/generate-chart', methods=['POST'])
def generate_chart():
    try:
        # 接收前端信息
        data = request.json
        name = data.get('name', 'User')
        year = data.get('year')
        month = data.get('month')
        day = data.get('day')
        hour = data.get('hour')
        minute = data.get('minute')
        input_method = data.get('input_method', 'city')
        city = data.get('city')
        state = data.get('state')
        country = data.get('country')
        latitude = data.get('latitude')
        longitude = data.get('longitude')
        timezone = data.get('timezone')  # UTC 偏移值（如 '-5'）

        # 验证年份、月份、日期和时间
        if not (1900 <= year <= 2025):
            return jsonify({'success': False, 'error': 'Year must be between 1900 and 2025'})
        if not (1 <= month <= 12):
            return jsonify({'success': False, 'error': 'Month must be between 1 and 12'})
        if not (1 <= day <= 31):
            return jsonify({'success': False, 'error': 'Day must be between 1 and 31'})
        if not (0 <= hour <= 23):
            return jsonify({'success': False, 'error': 'Hour must be between 0 and 23'})
        if not (0 <= minute <= 59):
            return jsonify({'success': False, 'error': 'Minute must be between 0 and 59'})

        # 验证日期有效性
        try:
            datetime(year, month, day)
        except ValueError:
            return jsonify({'success': False, 'error': 'Invalid date'})

        # 处理位置信息
        if input_method == 'manual':
            if latitude is None or longitude is None or not timezone:
                return jsonify({'success': False, 'error': 'Latitude, longitude, and timezone are required for manual input'})
            if not (-90 <= latitude <= 90):
                return jsonify({'success': False, 'error': 'Latitude must be between -90 and 90'})
            if not (-180 <= longitude <= 180):
                return jsonify({'success': False, 'error': 'Longitude must be between -180 and 180'})
        else:
            # 使用城市、国家、州
            if not country or not state or not city or not timezone:
                return jsonify({'success': False, 'error': 'Country, state, city, and timezone are required'})
            if country not in city_data:
                return jsonify({'success': False, 'error': f'Country {country} not found in city data'})
            city_info = next((c for c in city_data[country] if c['city'] == city and c['state'] == state), None)
            if not city_info:
                return jsonify({'success': False, 'error': f'City {city}, {state} not found in {country}'})
            latitude = city_info['lat']
            longitude = city_info['lng']

        # 验证时区并转换为 pytz 时区
        if timezone not in utc_to_pytz:
            return jsonify({'success': False, 'error': f'Invalid timezone offset: {timezone}'})
        pytz_timezone = utc_to_pytz[timezone]
        try:
            pytz.timezone(pytz_timezone)
        except pytz.exceptions.UnknownTimeZoneError:
            return jsonify({'success': False, 'error': f'Invalid pytz timezone: {pytz_timezone}'})

        # 生成星盘数据
        try:
            subject = AstrologicalSubject(name, year, month, day, hour, minute, lng=longitude, lat=latitude, tz_str=pytz_timezone)
        except Exception as e:
            logger.error(f"Failed to create AstrologicalSubject: {str(e)}")
            return jsonify({'success': False, 'error': f'Failed to generate chart: Invalid parameters'})

        chart = KerykeionChartSVG(subject)
        svg_data = chart.makeTemplate(minify=True)

        # 手动构建行星数据（包括上升星座）
        planets = [
            {'name': 'Ascendant', 'sign': subject.first_house.sign, 'degree': subject.first_house.position, 'house': 'First_House'},
            {'name': 'Sun', 'sign': subject.sun.sign, 'degree': subject.sun.position, 'house': subject.sun.house},
            {'name': 'Moon', 'sign': subject.moon.sign, 'degree': subject.moon.position, 'house': subject.moon.house},
            {'name': 'Mercury', 'sign': subject.mercury.sign, 'degree': subject.mercury.position, 'house': subject.mercury.house},
            {'name': 'Venus', 'sign': subject.venus.sign, 'degree': subject.venus.position, 'house': subject.venus.house},
            {'name': 'Mars', 'sign': subject.mars.sign, 'degree': subject.mars.position, 'house': subject.mars.house},
            {'name': 'Jupiter', 'sign': subject.jupiter.sign, 'degree': subject.jupiter.position, 'house': subject.jupiter.house},
            {'name': 'Saturn', 'sign': subject.saturn.sign, 'degree': subject.saturn.position, 'house': subject.saturn.house},
            {'name': 'Uranus', 'sign': subject.uranus.sign, 'degree': subject.uranus.position, 'house': subject.uranus.house},
            {'name': 'Neptune', 'sign': subject.neptune.sign, 'degree': subject.neptune.position, 'house': subject.neptune.house},
            {'name': 'Pluto', 'sign': subject.pluto.sign, 'degree': subject.pluto.position, 'house': subject.pluto.house}
        ]

        # 清理宫位名称
        house_names = {
            'First': 'First_House', 'Second': 'Second_House', 'Third': 'Third_House', 'Fourth': 'Fourth_House',
            'Fifth': 'Fifth_House', 'Sixth': 'Sixth_House', 'Seventh': 'Seventh_House', 'Eighth': 'Eighth_House',
            'Ninth': 'Ninth_House', 'Tenth': 'Tenth_House', 'Eleventh': 'Eleventh_House', 'Twelfth': 'Twelfth_House'
        }
        for planet in planets:
            if planet['name'] != 'Ascendant':
                for key, value in house_names.items():
                    if planet['house'].startswith(key):
                        planet['house'] = value
                        break

        # 提取宫位数据
        try:
            houses = [
                {
                    'house': i + 1,
                    'sign': house['sign'],
                    'degree': house['position']
                } for i, house in enumerate(subject.houses_list)
            ]
        except AttributeError:
            logger.warning("subject.houses_list not found. Skipping houses data.")
            houses = []

        # 相位类型映射
        aspect_type_mapping = {
            'Conjunction': 'conjunct',
            'Opposition': 'opposition',
            'Square': 'square',
            'Trine': 'trine',
            'Sextile': 'sextile'
        }

        # 使用 kerykeion 的内置相位计算
        aspects = []
        try:
            for aspect in subject.aspects_list:
                aspects.append({
                    'planet1': aspect['p1_name'],
                    'planet2': aspect['p2_name'],
                    'aspect': aspect['aspect_type'],
                    'orb': aspect['orb']
                })
        except AttributeError:
            logger.warning("subject.aspects_list not found. Using manual aspect calculation.")
            planet_list = [subject.first_house, subject.sun, subject.moon, subject.mercury, subject.venus, subject.mars,
                           subject.jupiter, subject.saturn, subject.uranus, subject.neptune, subject.pluto]
            planet_names = ['Ascendant', 'Sun', 'Moon', 'Mercury', 'Venus', 'Mars', 'Jupiter', 'Saturn', 'Uranus', 'Neptune', 'Pluto']
            for i, p1 in enumerate(planet_list):
                for j, p2 in enumerate(planet_list[i+1:], start=i+1):
                    angle = abs(p1.position - p2.position)
                    if angle > 180: angle = 360 - angle
                    orb = angle
                    aspect_type = None
                    if orb < 10:
                        aspect_type = 'conjunct'
                    elif abs(orb - 180) < 10:
                        aspect_type = 'opposition'
                    elif abs(orb - 90) < 10:
                        aspect_type = 'square'
                    elif abs(orb - 120) < 10:
                        aspect_type = 'trine'
                    elif abs(orb - 60) < 10:
                        aspect_type = 'sextile'
                    if aspect_type:
                        aspects.append({
                            'planet1': planet_names[i],
                            'planet2': planet_names[j],
                            'aspect': aspect_type,
                            'orb': orb
                        })

        # 调试相位计算
        logger.debug("Calculated Aspects: %s", [
            f"{aspect['planet1']} {aspect['aspect']} {aspect['planet2']} (Orb: {aspect['orb']:.2f}°)"
            for aspect in aspects
        ])

        # 行星和上升星座解读
        planet_interpretations = []
        for planet in planets:
            house = planet['house']
            display_house = house.replace('_House', ' House')
            if planet['name'] in interpretations['planets_in_houses'] and house in interpretations['planets_in_houses'][planet['name']]:
                text = interpretations['planets_in_houses'][planet['name']][house]
                planet_interpretations.append(f"{planet['name']} in {display_house}: {text}")
            else:
                planet_interpretations.append(f"{planet['name']} in {display_house}: No interpretation available.")

        # 相位解读
        aspect_interpretations = interpretations.get('aspects', {})
        logger.debug("Available aspect keys: %s", list(aspect_interpretations.keys()))
        aspects_text = []
        for aspect in aspects:
            mapped_aspect = aspect_type_mapping.get(aspect['aspect'], aspect['aspect'].lower())
            specific_key = f"{aspect['planet1']}_{mapped_aspect}_{aspect['planet2']}"
            reverse_key = f"{aspect['planet2']}_{mapped_aspect}_{aspect['planet1']}"
            text = aspect_interpretations.get(specific_key) or aspect_interpretations.get(reverse_key, f"No interpretation available for {aspect['planet1']} {aspect['aspect']} {aspect['planet2']}.")
            aspects_text.append(text)

        # 调试输出
        logger.debug("Planets: %s", planets)
        logger.debug("Houses: %s", houses)
        logger.debug("Aspects: %s", aspects)
        logger.debug("Planet Interpretations: %s", planet_interpretations)
        logger.debug("Aspects Text: %s", aspects_text)
        logger.debug("Available Planet Interpretations: %s", {planet: list(houses.keys()) for planet, houses in interpretations['planets_in_houses'].items()})
        logger.debug("Available Aspects: %s", list(aspect_interpretations.keys()))

        return jsonify({
            'success': True,
            'svg': svg_data,
            'planets': planets,
            'houses': houses,
            'aspects': aspects,
            'planet_interpretations': planet_interpretations,
            'aspects_text': aspects_text
        })
    except Exception as e:
        logger.error("Error in generate_chart: %s", str(e))
        return jsonify({
            'success': False,
            'error': str(e)
        })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)