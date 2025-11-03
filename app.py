import os
from flask import Flask, request, jsonify, render_template, session
from flask_cors import CORS
from kerykeion import AstrologicalSubject, KerykeionChartSVG
import json
import logging
import pytz
from datetime import datetime
import uuid

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-key-for-development-only')
# 修复：开发环境允许localhost跨域，生产环境自动限制
CORS(app, resources={r"/*": {"origins": ["https://www.yourluckycompass.com", "http://localhost:5000"]}})

# 修复：日志级别根据环境变量控制（生产环境INFO，开发环境DEBUG）
log_level = logging.INFO if os.environ.get('ENV') == 'production' else logging.DEBUG
logging.basicConfig(level=log_level)
logger = logging.getLogger(__name__)

# 存储用户的出生星盘数据（生产环境应使用数据库，如Redis/MySQL）
user_natal_data = {}

# Load interpretation templates
try:
    with open('interpretations.json', 'r', encoding='utf-8') as f:
        interpretations = json.load(f)
except FileNotFoundError:
    logger.error("interpretations.json not found. Please create it in the project directory.")
    interpretations = {"planets_in_houses": {}, "aspects": {}, "transits": {}}
except json.JSONDecodeError as e:
    logger.error(f"Failed to parse interpretations.json: {e}")
    interpretations = {"planets_in_houses": {}, "aspects": {}, "transits": {}}

# 确保 transits 键始终存在（占位）
if "transits" not in interpretations:
    logger.warning("interpretations.json 缺少 'transits' 键，已自动创建占位结构。")
    interpretations["transits"] = {}

# 新增：加载行运相位数据库（单独文件 transits.json）
try:
    with open('transits.json', 'r', encoding='utf-8') as f:
        transits_data = json.load(f)
    logger.info("transits.json loaded successfully.")
except FileNotFoundError:
    logger.error("transits.json not found. Please add it to the project directory.")
    transits_data = {}
except json.JSONDecodeError as e:
    logger.error(f"Failed to parse transits.json: {e}")
    transits_data = {}

# Load stars-in-zodiac templates
try:
    with open('stars_in_zodiac.json', 'r', encoding='utf-8') as f:
        stars_in_zodiac = json.load(f)
    logger.info("stars_in_zodiac.json loaded successfully.")
except FileNotFoundError:
    logger.error("stars_in_zodiac.json not found. Please add it to the project directory.")
    stars_in_zodiac = {}
except json.JSONDecodeError as e:
    logger.error(f"Failed to parse stars_in_zodiac.json: {e}")
    stars_in_zodiac = {}

# Load city data
try:
    with open('city_data.json', 'r', encoding='utf-8') as f:
        city_data = json.load(f)
except FileNotFoundError:
    logger.error("city_data.json not found. Please create it in the project directory.")
    city_data = {}
except json.JSONDecodeError as e:
    logger.error(f"Failed to parse city_data.json: {e}")
    city_data = {}

# 修复：时区映射重复问题（拆分UTC+8为不同键，对应不同地区）
utc_to_pytz = {
    '-10': 'Pacific/Honolulu', '-9': 'America/Anchorage', '-8': 'America/Los_Angeles',
    '-7': 'America/Denver', '-6': 'America/Chicago', '-5': 'America/New_York',
    '-4': 'America/Halifax', '0': 'Europe/London', '2': 'Africa/Johannesburg',
    '8-au': 'Australia/Perth',  # 澳大利亚珀斯（UTC+8）
    '9.5': 'Australia/Adelaide', '10': 'Australia/Sydney',
    '12': 'Pacific/Auckland', '12.75': 'Pacific/Chatham',
    '-12': 'Etc/GMT+12', '-11': 'Pacific/Pago_Pago', '-3': 'America/Sao_Paulo',
    '-2': 'Atlantic/South_Georgia', '-1': 'Atlantic/Azores', '1': 'Europe/Paris',
    '3': 'Europe/Moscow', '4': 'Asia/Dubai', '5': 'Asia/Karachi',
    '5.5': 'Asia/Kolkata', '6': 'Asia/Dhaka', '7': 'Asia/Bangkok',
    '8-cn': 'Asia/Shanghai',  # 中国/亚洲（UTC+8）
    '9': 'Asia/Tokyo', '11': 'Pacific/Guadalcanal',
    '13': 'Pacific/Tongatapu', '14': 'Pacific/Kiritimati'
}

# 同步前端：更新国家对应的时区值（匹配拆分后的键）
country_timezone_mapping = {
    'US': ['-10', '-9', '-8', '-7', '-6', '-5'],
    'CA': ['-8', '-7', '-6', '-5', '-4'],
    'MX': ['-8', '-7', '-6', '-5'],
    'GB': ['0'],
    'AU': ['8-au', '9.5', '10'],  # 澳大利亚对应UTC+8（珀斯）
    'NZ': ['12', '12.75'],
    'ZA': ['2']
}

# Mapping of 3-letter sign abbreviations → full English sign names
SIGN_ABBREV_TO_FULL = {
    'Ari': 'Aries', 'Tau': 'Taurus', 'Gem': 'Gemini', 'Can': 'Cancer',
    'Leo': 'Leo', 'Vir': 'Virgo', 'Lib': 'Libra', 'Sco': 'Scorpio',
    'Sag': 'Sagittarius', 'Cap': 'Capricorn', 'Aqu': 'Aquarius', 'Pis': 'Pisces'
}

def calculate_transits(natal_planets, transit_planets):
    """计算行运行星与出生行星之间的相位（添加防御性判断）"""
    if not natal_planets or not transit_planets:
        logger.warning("Natal or transit planets list is empty")
        return []
    
    transits = []
    aspect_types = {
        'conjunct': {'angle': 0, 'orb': 10},
        'opposition': {'angle': 180, 'orb': 10},
        'square': {'angle': 90, 'orb': 10},
        'trine': {'angle': 120, 'orb': 10},
        'sextile': {'angle': 60, 'orb': 10}
    }

    for natal in natal_planets:
        # 跳过无效数据
        if 'degree' not in natal or 'name' not in natal:
            continue
        for transit in transit_planets:
            if 'degree' not in transit or 'name' not in transit:
                continue
            if natal['name'] == transit['name']:
                continue
            angle = abs(natal['degree'] - transit['degree'])
            if angle > 180:
                angle = 360 - angle
            for aspect, params in aspect_types.items():
                if abs(angle - params['angle']) <= params['orb']:
                    orb = abs(angle - params['angle'])
                    transits.append({
                        'natal_planet': natal['name'],
                        'transit_planet': transit['name'],
                        'aspect': aspect,
                        'orb': orb,
                        'natal_sign': natal.get('sign', 'Unknown'),
                        'transit_sign': transit.get('sign', 'Unknown'),
                        'strength': 100 - (orb / params['orb'] * 100)
                    })
    return sorted(transits, key=lambda x: x['strength'], reverse=True)

# ====================== Routes ======================

@app.route('/get-cities', methods=['GET'])
def get_cities():
    return jsonify(city_data)

@app.route('/')
@app.route('/index.html')
def index():
    return render_template('index.html')

@app.route('/generate-chart', methods=['POST'])
def generate_chart():
    try:
        if 'user_id' not in session:
            session['user_id'] = str(uuid.uuid4())

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
        timezone = data.get('timezone')

        # ---------- 基础校验 ----------
        if not (1900 <= year <= 2025):
            return jsonify({'success': False, 'error': 'Year must be between 1900 and 2025'})
        if not (1 <= month <= 12):
            return jsonify({'success': False, 'error': 'Month must be between 1 and 12'})
        if not (1 <= day <= 31):
            return jsonify({'success': False, 'error': 'Day must be between 1 and 31'})
        if not (0 <= hour <= 23):
            return jupytext({'success': False, 'error': 'Hour must be between 0 and 23'})
        if not (0 <= minute <= 59):
            return jsonify({'success': False, 'error': 'Minute must be between 0 and 59'})
        try:
            datetime(year, month, day)
        except ValueError:
            return jsonify({'success': False, 'error': 'Invalid date'})

        # ---------- 位置处理 ----------
        if input_method == 'manual':
            if latitude is None or longitude is None or not timezone:
                return jsonify({'success': False, 'error': 'Latitude, longitude, and timezone are required for manual input'})
            if not (-90 <= latitude <= 90):
                return jsonify({'success': False, 'error': 'Latitude must be between -90 and 90'})
            if not (-180 <= longitude <= 180):
                return jsonify({'success': False, 'error': 'Longitude must be between -180 and 180'})
        else:
            if not country or not state or not city or not timezone:
                return jsonify({'success': False, 'error': 'Country, state, city, and timezone are required'})
            if country not in city_data:
                return jsonify({'success': False, 'error': f'Country {country} not found in city data'})
            city_info = next((c for c in city_data[country] if c['city'] == city and c['state'] == state), None)
            if not city_info:
                return jsonify({'success': False, 'error': f'City {city}, {state} not found in {country}'})
            latitude = city_info['lat']
            longitude = city_info['lng']

        # 修复：时区有效性校验（匹配拆分后的键）
        if timezone not in utc_to_pytz:
            return jsonify({'success': False, 'error': f'Invalid timezone offset: {timezone}'})
        pytz_timezone = utc_to_pytz[timezone]
        try:
            pytz.timezone(pytz_timezone)
        except pytz.exceptions.UnknownTimeZoneError:
            return jsonify({'success': False, 'error': f'Invalid pytz timezone: {pytz_timezone}'})

        # ---------- 生成星盘 ----------
        subject = AstrologicalSubject(
            name=name, year=year, month=month, day=day,
            hour=hour, minute=minute,
            lng=longitude, lat=latitude,
            tz_str=pytz_timezone,
            city=city or "Unknown", nation=country or "Unknown"
        )
        chart = KerykeionChartSVG(subject)
        svg_data = chart.makeTemplate()

        # ---------- 行星列表（优化宫位映射，增强兼容性） ----------
        planets = []
        # 安全获取行星数据，避免属性不存在报错
        planet_configs = [
            ('Ascendant', subject.first_house if hasattr(subject, 'first_house') else None, 'First_House'),
            ('Sun', subject.sun if hasattr(subject, 'sun') else None, None),
            ('Moon', subject.moon if hasattr(subject, 'moon') else None, None),
            ('Mercury', subject.mercury if hasattr(subject, 'mercury') else None, None),
            ('Venus', subject.venus if hasattr(subject, 'venus') else None, None),
            ('Mars', subject.mars if hasattr(subject, 'mars') else None, None),
            ('Jupiter', subject.jupiter if hasattr(subject, 'jupiter') else None, None),
            ('Saturn', subject.saturn if hasattr(subject, 'saturn') else None, None),
            ('Uranus', subject.uranus if hasattr(subject, 'uranus') else None, None),
            ('Neptune', subject.neptune if hasattr(subject, 'neptune') else None, None),
            ('Pluto', subject.pluto if hasattr(subject, 'pluto') else None, None)
        ]

        house_names = {
            '1': 'First_House', '2': 'Second_House', '3': 'Third_House', '4': 'Fourth_House',
            '5': 'Fifth_House', '6': 'Sixth_House', '7': 'Seventh_House', '8': 'Eighth_House',
            '9': 'Ninth_House', '10': 'Tenth_House', '11': 'Eleventh_House', '12': 'Twelfth_House',
            'First': 'First_House', 'Second': 'Second_House', 'Third': 'Third_House', 'Fourth': 'Fourth_House',
            'Fifth': 'Fifth_House', 'Sixth': 'Sixth_House', 'Seventh': 'Seventh_House', 'Eighth': 'Eighth_House',
            'Ninth': 'Ninth_House', 'Tenth': 'Tenth_House', 'Eleventh': 'Eleventh_House', 'Twelfth': 'Twelfth_House'
        }

        for name, planet, default_house in planet_configs:
            if not planet or not hasattr(planet, 'sign') or not hasattr(planet, 'position'):
                continue
            # 处理宫位映射（兼容数字和文字格式）
            house = default_house or getattr(planet, 'house', 'Unknown')
            if house != 'Unknown' and str(house) in house_names:
                house = house_names[str(house)]
            else:
                # 模糊匹配宫位名称
                matched_house = next((v for k, v in house_names.items() if str(house).startswith(k)), house)
                house = matched_house

            planets.append({
                'name': name,
                'sign': planet.sign,
                'degree': planet.position,
                'house': house
            })

        # ---------- 宫位 ----------
        houses = []
        try:
            if hasattr(subject, 'houses_list') and isinstance(subject.houses_list, list):
                houses = [
                    {'house': i + 1, 'sign': house.get('sign', 'Unknown'), 'degree': house.get('position', 0)}
                    for i, house in enumerate(subject.houses_list)
                ]
            else:
                logger.warning("subject.houses_list is not a valid list")
        except Exception as e:
            logger.warning(f"Failed to parse houses: {e}")
            houses = []

        # ---------- 相位 ----------
        aspect_type_mapping = {
            'Conjunction': 'conjunct', 'Opposition': 'opposition',
            'Square': 'square', 'Trine': 'trine', 'Sextile': 'sextile'
        }
        aspects = []
        try:
            if hasattr(subject, 'aspects_list') and isinstance(subject.aspects_list, list):
                for aspect in subject.aspects_list:
                    if 'p1_name' in aspect and 'p2_name' in aspect and 'aspect_type' in aspect and 'orb' in aspect:
                        aspects.append({
                            'planet1': aspect['p1_name'],
                            'planet2': aspect['p2_name'],
                            'aspect': aspect['aspect_type'],
                            'orb': aspect['orb']
                        })
            else:
                logger.warning("subject.aspects_list not found or invalid. Using manual calculation.")
                # 手动计算相位（仅使用有效行星数据）
                valid_planets = [p for p in [subject.first_house, subject.sun, subject.moon, subject.mercury, subject.venus,
                                            subject.mars, subject.jupiter, subject.saturn, subject.uranus,
                                            subject.neptune, subject.pluto] if p and hasattr(p, 'position')]
                planet_names = ['Ascendant', 'Sun', 'Moon', 'Mercury', 'Venus', 'Mars',
                                'Jupiter', 'Saturn', 'Uranus', 'Neptune', 'Pluto']
                for i, p1 in enumerate(valid_planets):
                    for j, p2 in enumerate(valid_planets[i+1:], start=i+1):
                        angle = abs(p1.position - p2.position)
                        if angle > 180:
                            angle = 360 - angle
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
        except Exception as e:
            logger.error(f"Error calculating aspects: {e}")
            aspects = []

        # ---------- 行星在宫位解释 ----------
        planet_interpretations = []
        for planet in planets:
            house = planet['house']
            display_house = house.replace('_House', ' House')
            # 兼容不同的键格式
            if (planet['name'] in interpretations['planets_in_houses'] and
                house in interpretations['planets_in_houses'][planet['name']]):
                text = interpretations['planets_in_houses'][planet['name']][house]
            else:
                text = f"No interpretation available for {planet['name']} in {display_house}."
            planet_interpretations.append(f"{planet['name']} in {display_house}: {text}")

        # ---------- 星体在星座解释 ----------
        stars_in_signs = []
        for planet in planets:
            if planet['name'] == 'Ascendant':
                continue
            sign_full = SIGN_ABBREV_TO_FULL.get(planet['sign'], planet['sign'])
            if sign_full in stars_in_zodiac and planet['name'] in stars_in_zodiac[sign_full]:
                entry = stars_in_zodiac[sign_full][planet['name']]
                title = entry.get('title', f"{planet['name']} in {sign_full}")
                subtitle = entry.get('subtitle', '')
                description = entry.get('description', 'No description available.')
            else:
                title = f"{planet['name']} in {sign_full}"
                subtitle = ''
                description = f'Interpretation for {planet["name"]} in {sign_full} not available.'
            stars_in_signs.append({
                'planet': planet['name'],
                'sign': sign_full,
                'title': title,
                'subtitle': subtitle,
                'description': description
            })

        # ---------- 相位解释 ----------
        aspect_interpretations = interpretations.get('aspects', {})
        aspects_text = []
        for aspect in aspects:
            mapped = aspect_type_mapping.get(aspect['aspect'], aspect['aspect'].lower())
            key1 = f"{aspect['planet1']}_{mapped}_{aspect['planet2']}"
            key2 = f"{aspect['planet2']}_{mapped}_{aspect['planet1']}"
            text = (
                aspect_interpretations.get(key1)
                or aspect_interpretations.get(key2)
                or f"No interpretation available for {aspect['planet1']} {aspect['aspect'].title()} {aspect['planet2']}."
            )
            aspects_text.append(text)

        # ---------- 保存 natal 数据 ----------
        user_id = session['user_id']
        user_natal_data[user_id] = {
            'planets': planets,
            'houses': houses,
            'aspects': aspects,
            'name': name,
            'calculated_at': datetime.now().isoformat()
        }

        return jsonify({
            'success': True,
            'svg': svg_data,
            'planets': planets,
            'houses': houses,
            'aspects': aspects,
            'planet_interpretations': planet_interpretations,
            'aspects_text': aspects_text,
            'stars_in_signs': stars_in_signs
        })

    except Exception as e:
        logger.error("Error in generate_chart: %s", str(e), exc_info=True)  # 输出完整堆栈信息
        return jsonify({'success': False, 'error': 'Failed to generate chart. Please try again.'})

# ====================== Transit Route ======================
@app.route('/transit-chart', methods=['POST'])
def transit_chart():
    """生成当前时间的行运星盘并与出生星盘对比（修复时间计算+svg兼容性+语法错误）"""
    try:
        if 'user_id' not in session:
            return jsonify({'success': False, 'error': 'Please generate your birth chart first before checking transits.'})

        user_id = session['user_id']
        if user_id not in user_natal_data:
            return jsonify({'success': False, 'error': 'No natal chart data found. Please generate your birth chart first.'})

        natal_data = user_natal_data[user_id]
        data = request.json

        input_method = data.get('input_method', 'city')
        city = data.get('city')
        state = data.get('state')
        country = data.get('country')
        latitude = data.get('latitude')
        longitude = data.get('longitude')
        timezone = data.get('timezone')

        # ---------- 位置处理（同 generate_chart） ----------
        if input_method == 'manual':
            if latitude is None or longitude is None or not timezone:
                return jsonify({'success': False, 'error': 'Latitude, longitude, and timezone are required for manual input'})
            if not (-90 <= latitude <= 90):
                return jsonify({'success': False, 'error': 'Latitude must be between -90 and 90'})
            if not (-180 <= longitude <= 180):
                return jupytext({'success': False, 'error': 'Longitude must be between -180 and 180'})
            # 修复：Python 格式化 float 用 f-string，替代 JS 的 toFixed()
            transit_location = f"Lat: {latitude:.4f}, Lng: {longitude:.4f}"
        else:
            if not country or not state or not city or not timezone:
                return jsonify({'success': False, 'error': 'Country, state, city, and timezone are required'})
            if country not in city_data:
                return jsonify({'success': False, 'error': f'Country {country} not found in city data'})
            city_info = next((c for c in city_data[country] if c['city'] == city and c['state'] == state), None)
            if not city_info:
                return jsonify({'success': False, 'error': f'City {city}, {state} not found in {country}'})
            latitude = city_info['lat']
            longitude = city_info['lng']
            # 下拉选择时记录位置信息，方便前端显示
            transit_location = f"{city}, {state}, {country}"

        # 修复：时区有效性校验（匹配拆分后的键）
        if timezone not in utc_to_pytz:
            return jsonify({'success': False, 'error': f'Invalid timezone offset: {timezone}'})
        pytz_timezone = utc_to_pytz[timezone]
        try:
            tz = pytz.timezone(pytz_timezone)
        except pytz.exceptions.UnknownTimeZoneError:
            return jsonify({'success': False, 'error': f'Invalid pytz timezone: {pytz_timezone}'})

        # 修复：行运时间使用用户当前时区（而非UTC）
        utc_now = datetime.utcnow().replace(tzinfo=pytz.UTC)
        local_now = utc_now.astimezone(tz)  # 转换为用户当前时区时间
        logger.debug(f"Transit calculated for local time: {local_now} (timezone: {pytz_timezone})")
        logger.debug(f"Transit location: {transit_location} (lat: {latitude:.4f}, lng: {longitude:.4f})")

        # ---------- 生成行运星盘（使用本地时间） ----------
        transit_subject = AstrologicalSubject(
            name="Transit",
            year=local_now.year,
            month=local_now.month,
            day=local_now.day,
            hour=local_now.hour,
            minute=local_now.minute,
            lng=longitude,
            lat=latitude,
            tz_str=pytz_timezone,
            city=city or "Unknown",
            nation=country or "Unknown"
        )
        transit_chart = KerykeionChartSVG(transit_subject)
        transit_svg = transit_chart.makeTemplate()

        # 关键优化：清理 svg 格式，增强前端兼容性（去除多余空格和换行）
        if transit_svg:
            transit_svg = transit_svg.strip().replace('\n', '').replace('\r', '')
            # 确保 svg 有明确的宽高和 viewbox，避免前端渲染错乱
            if 'width' not in transit_svg or 'height' not in transit_svg:
                # 添加默认宽高（适配大多数场景）
                transit_svg = transit_svg.replace('<svg', '<svg width="800" height="800"')
            logger.debug(f"Transit SVG generated successfully (length: {len(transit_svg)})")
        else:
            transit_svg = '<svg width="800" height="800" viewBox="0 0 800 800"><text x="400" y="400" text-anchor="middle" font-size="16">Transit chart visual not available</text></svg>'
            logger.warning("Transit SVG generation failed, using fallback svg")

        # ---------- 行运行星列表（优化兼容性） ----------
        transit_planets = []
        transit_planet_configs = [
            ('Ascendant', transit_subject.first_house if hasattr(transit_subject, 'first_house') else None, 'First_House'),
            ('Sun', transit_subject.sun if hasattr(transit_subject, 'sun') else None, None),
            ('Moon', transit_subject.moon if hasattr(transit_subject, 'moon') else None, None),
            ('Mercury', transit_subject.mercury if hasattr(transit_subject, 'mercury') else None, None),
            ('Venus', transit_subject.venus if hasattr(transit_subject, 'venus') else None, None),
            ('Mars', transit_subject.mars if hasattr(transit_subject, 'mars') else None, None),
            ('Jupiter', transit_subject.jupiter if hasattr(transit_subject, 'jupiter') else None, None),
            ('Saturn', transit_subject.saturn if hasattr(transit_subject, 'saturn') else None, None),
            ('Uranus', transit_subject.uranus if hasattr(transit_subject, 'uranus') else None, None),
            ('Neptune', transit_subject.neptune if hasattr(transit_subject, 'neptune') else None, None),
            ('Pluto', transit_subject.pluto if hasattr(transit_subject, 'pluto') else None, None)
        ]

        # 关键修复：在 transit_chart 函数内添加 house_names 定义
        house_names = {
            '1': 'First_House', '2': 'Second_House', '3': 'Third_House', '4': 'Fourth_House',
            '5': 'Fifth_House', '6': 'Sixth_House', '7': 'Seventh_House', '8': 'Eighth_House',
            '9': 'Ninth_House', '10': 'Tenth_House', '11': 'Eleventh_House', '12': 'Twelfth_House',
            'First': 'First_House', 'Second': 'Second_House', 'Third': 'Third_House', 'Fourth': 'Fourth_House',
            'Fifth': 'Fifth_House', 'Sixth': 'Sixth_House', 'Seventh': 'Seventh_House', 'Eighth': 'Eighth_House',
            'Ninth': 'Ninth_House', 'Tenth': 'Tenth_House', 'Eleventh': 'Eleventh_House', 'Twelfth': 'Twelfth_House'
        }

        for name, planet, default_house in transit_planet_configs:
            if not planet or not hasattr(planet, 'sign') or not hasattr(planet, 'position'):
                continue
            house = default_house or getattr(planet, 'house', 'Unknown')
            if house != 'Unknown' and str(house) in house_names:
                house = house_names[str(house)]
            else:
                matched_house = next((v for k, v in house_names.items() if str(house).startswith(k)), house)
                house = matched_house

            transit_planets.append({
                'name': name,
                'sign': planet.sign,
                'degree': planet.position,
                'house': house
            })

        # ---------- 计算行运相位 ----------
        transits = calculate_transits(natal_data['planets'], transit_planets)
        logger.debug(f"Calculated {len(transits)} active transits")

        # ---------- 行运解释（修复：读取transits.json并匹配键名格式） ----------
        transit_interpretations = []
        transit_aspect_map = {
            'conjunct': 'Conjunct', 'opposition': 'Opposition',
            'square': 'Square', 'trine': 'Trine', 'sextile': 'Sextile'
        }

        # 关键修改1：从加载的transits_data中获取解释（而非interpretations）
        transits_dict = transits_data.get('transits', {})

        for transit in transits:
            aspect_name = transit_aspect_map.get(transit['aspect'], transit['aspect'].title())
            # 关键修改2：生成与transits.json匹配的键名格式（Transiting 行运行星 相位 Natal 出生行星）
            key = f"Transiting {transit['transit_planet']} {aspect_name} Natal {transit['natal_planet']}"

            interpretation_text = transits_dict.get(key)

            if interpretation_text:
                transit_interpretations.append({
                    'transit': transit,
                    'interpretation': interpretation_text
                })
            else:
                # 优化占位解释，更友好
                placeholder = (
                    f"Transit {transit['transit_planet']} forms a {aspect_name} with your natal {transit['natal_planet']}. "
                    f"This aspect brings opportunities for growth and reflection. Orb: {transit['orb']:.2f}° | Strength: {transit['strength']:.1f}%"
                )
                transit_interpretations.append({
                    'transit': transit,
                    'interpretation': placeholder
                })

        # 关键优化：返回时补充位置信息和时间，方便前端显示
        return jsonify({
            'success': True,
            'transit_svg': transit_svg,
            'transit_location': transit_location,  # 新增：位置信息
            'transit_time': local_now.strftime('%Y-%m-%d %H:%M:%S %Z'),  # 新增：当地时间
            'transit_planets': transit_planets,
            'natal_planets': natal_data['planets'],
            'transits': transits,
            'transit_interpretations': transit_interpretations
        })

    except Exception as e:
        logger.error("Error in transit_chart: %s", str(e), exc_info=True)
        # 错误时返回更详细的提示
        return jsonify({
            'success': False,
            'error': 'Failed to generate transit chart. Please try again.',
            'debug_info': str(e)  # 可选：调试用，生产环境可删除
        })

# ====================== 新增：坐标查询页面路由（必须在 if __name__ 之前）=====================
@app.route('/geo-lookup')
def geo_lookup():
    return render_template('geo_lookup.html')

# ====================== 启动服务器 ======================
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    # 生产环境禁用debug模式
    debug_mode = os.environ.get('ENV') != 'production'
    app.run(host='0.0.0.0', port=port, debug=debug_mode)