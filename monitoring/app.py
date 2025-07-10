from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
import psycopg2
from psycopg2.extras import RealDictCursor
import json
from datetime import datetime, timedelta
import time
import os
import logging
import traceback

# Logging ni sozlash
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# PostgreSQL ulanish sozlamalari
DB_CONFIG = {
    'host': '172.18.33.254',
    'database': 'hmdm',
    'user': 'hmdm',
    'password': 'Ax@30-20',
    'port': '5432'
}

def get_db_connection():
    """PostgreSQL ga ulanish"""
    try:
        logger.info("Ma'lumotlar bazasiga ulanish: %s:%s/%s", DB_CONFIG['host'], DB_CONFIG['port'], DB_CONFIG['database'])
        conn = psycopg2.connect(**DB_CONFIG)
        logger.info("Ma'lumotlar bazasiga muvaffaqiyatli ulandi")
        return conn
    except psycopg2.OperationalError as e:
        logger.error("Ulanish xatosi: %s", str(e))
        logger.error("To'liq xato ma'lumoti: %s", traceback.format_exc())
        return None
    except Exception as e:
        logger.error("Kutilmagan xato: %s", str(e))
        logger.error("To'liq xato ma'lumoti: %s", traceback.format_exc())
        return None

def is_device_online(last_update_ts):
    """Qurilma online yoki offline ekanligini aniqlash"""
    if not last_update_ts:
        return False
    
    # Hozirgi vaqt (millisekund)
    current_time = int(time.time() * 1000)
    
    # 5 daqiqa = 300000 millisekund
    offline_threshold = 300000
    
    return (current_time - last_update_ts) < offline_threshold

def update_device_history(conn, device_id, is_online):
    """Qurilma holatini tarixga saqlash"""
    try:
        cursor = conn.cursor()
        
        # Avvalgi ochiq yozuvni yopish
        cursor.execute("""
            UPDATE device_history 
            SET end_time = CURRENT_TIMESTAMP 
            WHERE device_id = %s AND end_time IS NULL
        """, (device_id,))
        
        # Yangi yozuv qo'shish
        cursor.execute("""
            INSERT INTO device_history (device_id, status, start_time)
            VALUES (%s, %s, CURRENT_TIMESTAMP)
        """, (device_id, is_online))
        
        conn.commit()
        cursor.close()
    except Exception as e:
        logger.error("Device history update xatosi (device_id=%s): %s", device_id, str(e))
        logger.error("To'liq xato ma'lumoti: %s", traceback.format_exc())
        conn.rollback()

def get_device_stats(conn, device_id, period='all'):
    """Qurilma statistikasini olish"""
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        if period == 'daily':
            time_limit = "start_time >= CURRENT_DATE"
        elif period == 'weekly':
            time_limit = "start_time >= CURRENT_DATE - INTERVAL '7 days'"
        elif period == 'monthly':
            time_limit = "start_time >= CURRENT_DATE - INTERVAL '30 days'"
        else:
            time_limit = "TRUE"
        
        query = f"""
        WITH closed_periods AS (
            SELECT 
                device_id,
                status,
                start_time,
                COALESCE(end_time, CURRENT_TIMESTAMP) as end_time,
                EXTRACT(EPOCH FROM (COALESCE(end_time, CURRENT_TIMESTAMP) - start_time))/3600 as hours
            FROM device_history
            WHERE device_id = %s AND {time_limit}
        )
        SELECT 
            ROUND(SUM(CASE WHEN status = true THEN hours ELSE 0 END)::numeric, 2) as online_hours,
            ROUND(SUM(CASE WHEN status = false THEN hours ELSE 0 END)::numeric, 2) as offline_hours,
            ROUND(SUM(hours)::numeric, 2) as total_hours
        FROM closed_periods
        """
        
        cursor.execute(query, (device_id,))
        stats = cursor.fetchone()
        cursor.close()
        return stats
    except Exception as e:
        logger.error("Get device stats xatosi (device_id=%s): %s", device_id, str(e))
        logger.error("To'liq xato ma'lumoti: %s", traceback.format_exc())
        return None

def get_device_stats_by_date_range(conn, device_id, start_date, end_date):
    """Ma'lum sana oralig'i uchun qurilma statistikasini olish"""
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        # Kunlik statistikani olish
        query = """
        WITH daily_stats AS (
            SELECT 
                date_trunc('day', start_time)::date as day,
                bool_or(status) as had_online,
                SUM(
                    CASE 
                        WHEN status = true THEN 
                            EXTRACT(EPOCH FROM (
                                LEAST(COALESCE(end_time, CURRENT_TIMESTAMP), date_trunc('day', start_time) + INTERVAL '1 day') -
                                GREATEST(start_time, date_trunc('day', start_time))
                            ))/3600
                        ELSE 0 
                    END
                ) as online_hours,
                SUM(
                    CASE 
                        WHEN status = false THEN 
                            EXTRACT(EPOCH FROM (
                                LEAST(COALESCE(end_time, CURRENT_TIMESTAMP), date_trunc('day', start_time) + INTERVAL '1 day') -
                                GREATEST(start_time, date_trunc('day', start_time))
                            ))/3600
                        ELSE 0 
                    END
                ) as offline_hours
            FROM device_history
            WHERE device_id = %s 
            AND start_time >= %s::timestamp 
            AND (end_time IS NULL OR end_time <= %s::timestamp)
            GROUP BY date_trunc('day', start_time)::date
        )
        SELECT 
            day::text as date,
            had_online as online,
            ROUND(online_hours::numeric, 1) as online_hours,
            ROUND(offline_hours::numeric, 1) as offline_hours,
            ROUND((online_hours + offline_hours)::numeric, 1) as total_hours
        FROM daily_stats
        ORDER BY day
        """
        
        cursor.execute(query, (device_id, start_date, end_date))
        days = cursor.fetchall()
        
        # Kunlik statistikani dictionary formatiga o'tkazish
        stats = {}
        for day in days:
            stats[day['date']] = {
                'online': day['online'],
                'online_hours': day['online_hours'],
                'offline_hours': day['offline_hours'],
                'total_hours': day['total_hours']
            }
        
        cursor.close()
        return stats
    except Exception as e:
        logger.error("Get device stats by date range xatosi (device_id=%s): %s", device_id, str(e))
        logger.error("To'liq xato ma'lumoti: %s", traceback.format_exc())
        return None

@app.route('/')
def index():
    """Frontend sahifani ko'rsatish"""
    return send_from_directory('.', 'index.html')

@app.route('/favicon.ico')
def favicon():
    """Favicon ni ko'rsatish"""
    return send_from_directory('.', 'favicon.ico')

@app.route('/api/devices', methods=['GET'])
def get_devices():
    """Barcha qurilmalar ro'yxatini olish"""
    conn = get_db_connection()
    if not conn:
        logger.error("Qurilmalar ro'yxatini olishda database ulanish xatosi")
        return jsonify({
            'success': False,
            'error': 'Database ulanish xatosi',
            'details': 'Ma\'lumotlar bazasiga ulanib bo\'lmadi. Server loglarini tekshiring.'
        }), 500
    
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        # Qurilmalar va ularning holatini olish
        query = """
        SELECT 
            d.id,
            d.number,
            d.description,
            d.lastupdate,
            d.imei,
            d.phone,
            d.customerid,
            d.infojson,
            d.publicip,
            ds.configfilesstatus,
            ds.applicationsstatus
        FROM devices d
        LEFT JOIN devicestatuses ds ON d.id = ds.deviceid
        ORDER BY d.id
        """
        
        logger.info("Qurilmalar ro'yxatini olish uchun so'rov yuborilmoqda")
        cursor.execute(query)
        devices_data = cursor.fetchall()
        logger.info("%d ta qurilma topildi", len(devices_data))
        
        devices = []
        for device in devices_data:
            try:
                device_dict = dict(device)
                is_online = is_device_online(device['lastupdate'])
                device_dict['online'] = is_online
                device_dict['last_seen'] = datetime.fromtimestamp(device['lastupdate']/1000).strftime('%Y-%m-%d %H:%M:%S') if device['lastupdate'] else 'Never'
                
                # Qurilma holatini tarixga saqlash
                update_device_history(conn, device['id'], is_online)
                
                # Statistikani qo'shish
                stats = get_device_stats(conn, device['id'], 'daily')
                if stats:
                    device_dict['today_stats'] = stats
                
                stats = get_device_stats(conn, device['id'], 'weekly')
                if stats:
                    device_dict['weekly_stats'] = stats
                
                stats = get_device_stats(conn, device['id'], 'monthly')
                if stats:
                    device_dict['monthly_stats'] = stats
                
                devices.append(device_dict)
            except Exception as e:
                logger.error("Qurilma ma'lumotlarini qayta ishlashda xato (device_id=%s): %s", device.get('id'), str(e))
                logger.error("To'liq xato ma'lumoti: %s", traceback.format_exc())
        
        cursor.close()
        conn.close()
        
        return jsonify({
            'success': True,
            'devices': devices,
            'total_devices': len(devices),
            'online_devices': sum(1 for d in devices if d['online']),
            'offline_devices': sum(1 for d in devices if not d['online'])
        })
        
    except Exception as e:
        logger.error("Qurilmalar ro'yxatini olishda xato: %s", str(e))
        logger.error("To'liq xato ma'lumoti: %s", traceback.format_exc())
        return jsonify({
            'success': False,
            'error': 'Qurilmalar ro\'yxatini olishda xato',
            'details': str(e)
        }), 500

@app.route('/api/device/<int:device_id>', methods=['GET'])
def get_device_details(device_id):
    """Bitta qurilma haqida batafsil ma'lumot"""
    conn = get_db_connection()
    if not conn:
        logger.error("Qurilma haqida ma'lumotlar olishda database ulanish xatosi (device_id=%s)", device_id)
        return jsonify({'error': 'Database ulanish xatosi'}), 500
    
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        query = """
        SELECT 
            d.*,
            ds.configfilesstatus,
            ds.applicationsstatus
        FROM devices d
        LEFT JOIN devicestatuses ds ON d.id = ds.deviceid
        WHERE d.id = %s
        """
        
        cursor.execute(query, (device_id,))
        device_data = cursor.fetchone()
        
        if not device_data:
            logger.warning("Qurilma topilmadi (device_id=%s)", device_id)
            return jsonify({'error': 'Qurilma topilmadi'}), 404
        
        device = dict(device_data)
        device['online'] = is_device_online(device['lastupdate'])
        device['last_seen'] = datetime.fromtimestamp(device['lastupdate']/1000).strftime('%Y-%m-%d %H:%M:%S') if device['lastupdate'] else 'Never'
        
        cursor.close()
        conn.close()
        
        return jsonify({
            'success': True,
            'device': device
        })
        
    except Exception as e:
        logger.error("Qurilma haqida ma'lumotlar olishda xato (device_id=%s): %s", device_id, str(e))
        logger.error("To'liq xato ma'lumoti: %s", traceback.format_exc())
        return jsonify({'error': f'Ma\'lumotlarni olishda xato: {str(e)}'}), 500

@app.route('/api/statistics', methods=['GET'])
def get_statistics():
    """Umumiy statistika"""
    conn = get_db_connection()
    if not conn:
        logger.error("Umumiy statistika olishda database ulanish xatosi")
        return jsonify({'error': 'Database ulanish xatosi'}), 500
    
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        # Barcha qurilmalar soni
        cursor.execute("SELECT COUNT(*) as total FROM devices")
        total_devices = cursor.fetchone()['total']
        
        # Online qurilmalar soni
        current_time = int(time.time() * 1000)
        offline_threshold = 300000  # 5 daqiqa
        
        cursor.execute("""
            SELECT COUNT(*) as online_count 
            FROM devices 
            WHERE lastupdate > %s
        """, (current_time - offline_threshold,))
        
        online_devices = cursor.fetchone()['online_count']
        offline_devices = total_devices - online_devices
        
        # Oxirgi faoliyat
        cursor.execute("""
            SELECT number, lastupdate 
            FROM devices 
            WHERE lastupdate IS NOT NULL 
            ORDER BY lastupdate DESC 
            LIMIT 10
        """)
        
        recent_activity = []
        for row in cursor.fetchall():
            recent_activity.append({
                'device': row['number'],
                'last_seen': datetime.fromtimestamp(row['lastupdate']/1000).strftime('%Y-%m-%d %H:%M:%S')
            })
        
        cursor.close()
        conn.close()
        
        return jsonify({
            'success': True,
            'statistics': {
                'total_devices': total_devices,
                'online_devices': online_devices,
                'offline_devices': offline_devices,
                'online_percentage': round((online_devices / total_devices * 100) if total_devices > 0 else 0, 2),
                'recent_activity': recent_activity
            }
        })
        
    except Exception as e:
        logger.error("Umumiy statistika olishda xato: %s", str(e))
        logger.error("To'liq xato ma'lumoti: %s", traceback.format_exc())
        return jsonify({'error': f'Statistikani olishda xato: {str(e)}'}), 500

@app.route('/api/report', methods=['GET'])
def get_report():
    """Haftalik/oylik hisobot"""
    report_type = request.args.get('type', 'weekly')  # weekly yoki monthly
    
    conn = get_db_connection()
    if not conn:
        logger.error("Haftalik/oylik hisobot olishda database ulanish xatosi (report_type=%s)", report_type)
        return jsonify({'error': 'Database ulanish xatosi'}), 500
    
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        # Vaqt oralig'ini aniqlash
        if report_type == 'weekly':
            days_back = 7
        elif report_type == 'monthly':
            days_back = 30
        else:
            days_back = 7
        
        start_time = int((datetime.now() - timedelta(days=days_back)).timestamp() * 1000)
        
        # Qurilmalar faoliyati
        cursor.execute("""
            SELECT 
                d.id,
                d.number,
                d.description,
                d.lastupdate,
                CASE 
                    WHEN d.lastupdate > %s THEN 'active'
                    ELSE 'inactive'
                END as status_period
            FROM devices d
            ORDER BY d.lastupdate DESC
        """, (start_time,))
        
        devices_report = cursor.fetchall()
        
        active_devices = sum(1 for d in devices_report if d['status_period'] == 'active')
        inactive_devices = len(devices_report) - active_devices
        
        cursor.close()
        conn.close()
        
        return jsonify({
            'success': True,
            'report': {
                'type': report_type,
                'period': f"Oxirgi {days_back} kun",
                'total_devices': len(devices_report),
                'active_devices': active_devices,
                'inactive_devices': inactive_devices,
                'devices': [dict(d) for d in devices_report]
            }
        })
        
    except Exception as e:
        logger.error("Haftalik/oylik hisobot olishda xato (report_type=%s): %s", report_type, str(e))
        logger.error("To'liq xato ma'lumoti: %s", traceback.format_exc())
        return jsonify({'error': f'Hisobotni olishda xato: {str(e)}'}), 500

@app.route('/api/test-connection', methods=['GET'])
def test_connection():
    """Database ulanishini tekshirish"""
    try:
        conn = get_db_connection()
        if not conn:
            logger.error("Ma'lumotlar bazasiga ulanib bo'lmadi")
            return jsonify({
                'success': False,
                'error': 'Database ulanish xatosi',
                'details': 'Ma\'lumotlar bazasiga ulanib bo\'lmadi. Server loglarini tekshiring.'
            }), 500

        cursor = conn.cursor()
        
        # Version tekshirish
        cursor.execute('SELECT version();')
        version = cursor.fetchone()[0]
        logger.info("PostgreSQL versiyasi: %s", version)
        
        # Jadvallar ro'yxatini olish
        cursor.execute("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public'
        """)
        tables = [row[0] for row in cursor.fetchall()]
        logger.info("Mavjud jadvallar: %s", tables)
        
        cursor.close()
        conn.close()
        
        return jsonify({
            'success': True,
            'message': 'Database ulanishi muvaffaqiyatli',
            'version': version,
            'tables': tables
        })
        
    except Exception as e:
        error_msg = str(e)
        logger.error("Test ulanish xatosi: %s", error_msg)
        logger.error("To'liq xato ma'lumoti: %s", traceback.format_exc())
        return jsonify({
            'success': False,
            'error': 'Test ulanish xatosi',
            'details': error_msg
        }), 500

@app.route('/api/device-stats/<int:device_id>', methods=['GET'])
def get_device_date_range_stats(device_id):
    """Ma'lum sana oralig'i uchun qurilma statistikasini olish"""
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    
    if not start_date or not end_date:
        logger.warning("Ma'lum sana oralig'i uchun statistika olishda start_date yoki end_date parametri yo'q (device_id=%s)", device_id)
        return jsonify({'error': 'start_date va end_date parametrlari kerak'}), 400
    
    try:
        # Sanalarni tekshirish
        datetime.strptime(start_date, '%Y-%m-%d')
        datetime.strptime(end_date, '%Y-%m-%d')
    except ValueError:
        logger.warning("Sana formati noto'g'ri (start_date=%s, end_date=%s)", start_date, end_date)
        return jsonify({'error': 'Sana formati noto\'g\'ri. Format: YYYY-MM-DD'}), 400
    
    conn = get_db_connection()
    if not conn:
        logger.error("Ma'lum sana oralig'i uchun statistika olishda database ulanish xatosi (device_id=%s)", device_id)
        return jsonify({'error': 'Database ulanish xatosi'}), 500
    
    try:
        stats = get_device_stats_by_date_range(conn, device_id, start_date, end_date)
        conn.close()
        
        if stats is None:
            logger.error("Ma'lum sana oralig'i uchun statistika ma'lumotlarini olishda xatolik (device_id=%s, start_date=%s, end_date=%s)", device_id, start_date, end_date)
            return jsonify({'error': 'Statistika ma\'lumotlarini olishda xatolik'}), 500
        
        return jsonify({
            'success': True,
            'device_id': device_id,
            'start_date': start_date,
            'end_date': end_date,
            'stats': stats
        })
        
    except Exception as e:
        logger.error("Ma'lum sana oralig'i uchun statistika olishda xato (device_id=%s): %s", device_id, str(e))
        logger.error("To'liq xato ma'lumoti: %s", traceback.format_exc())
        return jsonify({'error': f'Statistikani olishda xato: {str(e)}'}), 500

if __name__ == '__main__':
    logger.info("Server ishga tushmoqda: %s:%s", '172.28.33.254', 5400)
    app.run(host='172.28.33.254', port=5400, debug=True)