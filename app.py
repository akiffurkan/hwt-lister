import os
import sys
import io
import json
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime
import webbrowser
from threading import Timer
from flask import Flask, jsonify, send_file, request, render_template_string, abort
from PIL import Image

# Dizin tanımları
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HWT_DIRS = [
    os.path.join(BASE_DIR, 'hwt'),
    os.path.join(BASE_DIR, '.hwt')
]
CACHE_DIR = os.path.join(BASE_DIR, 'cache')
os.makedirs(CACHE_DIR, exist_ok=True)
for d in HWT_DIRS:
    os.makedirs(d, exist_ok=True)

app = Flask(__name__)

def format_size(size_bytes):
    if size_bytes >= 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"
    elif size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    elif size_bytes >= 1024:
        return f"{size_bytes / 1024:.2f} KB"
    return f"{size_bytes} B"

def is_valid_image(data):
    """Görsel verisinin geçerli ve bozuk olmadığını kontrol eder."""
    if not data or len(data) < 200:
        return False
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.verify()
        return True
    except Exception:
        return False

def extract_safe_image(raw_bytes, dest_path):
    """Görseli web uyumlu RGB formatında kaydeder."""
    try:
        with Image.open(io.BytesIO(raw_bytes)) as im:
            # Şeffaflık varsa RGBA korunsun, yoksa RGB'ye dönüştürülsün
            if im.mode in ('RGBA', 'LA') or (im.mode == 'P' and 'transparency' in im.info):
                im = im.convert('RGBA')
                im.save(dest_path, format='PNG', optimize=True)
            else:
                im = im.convert('RGB')
                im.save(dest_path, format='JPEG', quality=92, optimize=True)
        return True
    except Exception:
        return False

def find_previews(zip_ref):
    """
    HWT arşivi içinden ana kapak görselini ve varsa AOD (bekleme) görselini bulur.
    AOD görselinin yanlışlıkla ana kapak olarak seçilmesini kesinlikle engeller!
    """
    entries = zip_ref.namelist()
    valid_entries = []
    
    # Sadece boyutu > 500 byte olan resim dosyalarını topla
    for entry in entries:
        if entry.endswith('/'):
            continue
        low = entry.lower()
        if any(low.endswith(ext) for ext in ('.jpg', '.jpeg', '.png', '.webp', '.bmp')):
            try:
                info = zip_ref.getinfo(entry)
                if info.file_size > 500:
                    valid_entries.append((entry, low, info.file_size))
            except Exception:
                pass

    cover_entry = None
    aod_entry = None

    # 1. AOD görselini ayrı tespit et
    for entry, low, sz in valid_entries:
        if 'aod' in low:
            aod_entry = entry
            break

    # 2. Ana Kapak Görselini (Cover) Öncelik Sırasıyla Bul (AOD hariç)
    # Öncelik 1: preview/cover.jpg veya preview/cover.png
    for entry, low, sz in valid_entries:
        if 'aod' in low:
            continue
        base = os.path.basename(low)
        if base in ('cover.jpg', 'cover.png', 'cover.jpeg', 'preview.png', 'preview.jpg'):
            cover_entry = entry
            break

    # Öncelik 2: preview/icon_small veya icon_small
    if not cover_entry:
        for entry, low, sz in valid_entries:
            if 'aod' in low:
                continue
            base = os.path.basename(low)
            if 'icon_small' in base or base in ('icon.png', 'icon.jpg'):
                cover_entry = entry
                break

    # Öncelik 3: 'preview' veya 'icon' içeren herhangi bir görsel
    if not cover_entry:
        for entry, low, sz in valid_entries:
            if 'aod' in low:
                continue
            if 'preview' in low or 'icon' in low or 'cover' in low:
                cover_entry = entry
                break

    # Öncelik 4: AOD olmayan en büyük görsel
    if not cover_entry:
        candidates = [x for x in valid_entries if 'aod' not in x[1]]
        if candidates:
            candidates.sort(key=lambda x: x[2], reverse=True)
            cover_entry = candidates[0][0]

    return cover_entry, aod_entry

def parse_xml_metadata(zip_ref):
    metadata = {}
    xml_files = [n for n in zip_ref.namelist() if n.lower().endswith('.xml')]
    
    xml_files.sort(key=lambda x: (
        0 if 'watch_face_config' in x.lower() else (
            1 if 'description' in x.lower() else 2
        )
    ))

    interesting_tags = {
        'title', 'name', 'author', 'designer', 'version', 'versionname',
        'versioncode', 'description', 'briefinfo', 'device', 'devicetype',
        'width', 'height', 'resolution', 'language', 'watchname', 'targetdevice'
    }

    for xml_file in xml_files:
        try:
            content = zip_ref.read(xml_file)
            root = ET.fromstring(content)
            
            file_meta = {}
            for elem in root.iter():
                tag = elem.tag.split('}')[-1].lower()
                if tag in interesting_tags and elem.text and elem.text.strip():
                    file_meta[tag] = elem.text.strip()
                
                for attr_key, attr_val in elem.attrib.items():
                    k_low = attr_key.split('}')[-1].lower()
                    if k_low in interesting_tags and attr_val.strip():
                        file_meta[f"{tag}_{k_low}"] = attr_val.strip()

            if file_meta:
                base_name = os.path.basename(xml_file)
                metadata[base_name] = file_meta
        except Exception:
            continue

    return metadata

def scan_hwt_files():
    items = []
    seen_filenames = set()

    for hwt_dir in HWT_DIRS:
        if not os.path.exists(hwt_dir):
            continue
        for fname in os.listdir(hwt_dir):
            if not fname.lower().endswith('.hwt'):
                continue
            if fname in seen_filenames:
                continue
            seen_filenames.add(fname)

            full_path = os.path.join(hwt_dir, fname)
            stat = os.stat(full_path)
            name_no_ext = os.path.splitext(fname)[0]
            mtime = datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')

            item = {
                'id': fname,
                'name': name_no_ext,
                'filename': fname,
                'size': format_size(stat.st_size),
                'size_bytes': stat.st_size,
                'modified': mtime,
                'has_preview': False,
                'preview_url': None,
                'has_aod': False,
                'aod_url': None,
                'image_count': 0,
                'file_count': 0,
                'xml_data': {},
                'file_list': [],
                'error': None
            }

            try:
                with zipfile.ZipFile(full_path, 'r') as zf:
                    namelist = [n for n in zf.namelist() if not n.endswith('/')]
                    item['file_count'] = len(namelist)
                    item['file_list'] = namelist[:150]
                    item['image_count'] = sum(1 for n in namelist if n.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.bmp')))
                    
                    # Kapak ve AOD görsellerini tespit et
                    cover_entry, aod_entry = find_previews(zf)

                    # Ana kapak görselini çıkart ve dönüştür
                    if cover_entry:
                        cache_cover_name = f"{name_no_ext}_cover.jpg"
                        cache_cover_path = os.path.join(CACHE_DIR, cache_cover_name)

                        # Önbellek yoksa veya eskiyse oluştur
                        if not os.path.exists(cache_cover_path) or os.path.getmtime(cache_cover_path) < stat.st_mtime:
                            raw_data = zf.read(cover_entry)
                            if extract_safe_image(raw_data, cache_cover_path):
                                item['has_preview'] = True
                                item['preview_url'] = f"/preview/{cache_cover_name}"
                        else:
                            item['has_preview'] = True
                            item['preview_url'] = f"/preview/{cache_cover_name}"

                    # AOD (Always on Display) bekleme görselini çıkart
                    if aod_entry:
                        cache_aod_name = f"{name_no_ext}_aod.jpg"
                        cache_aod_path = os.path.join(CACHE_DIR, cache_aod_name)

                        if not os.path.exists(cache_aod_path) or os.path.getmtime(cache_aod_path) < stat.st_mtime:
                            raw_data = zf.read(aod_entry)
                            if extract_safe_image(raw_data, cache_aod_path):
                                item['has_aod'] = True
                                item['aod_url'] = f"/preview/{cache_aod_name}"
                        else:
                            item['has_aod'] = True
                            item['aod_url'] = f"/preview/{cache_aod_name}"

                    # XML metadata çıkar
                    item['xml_data'] = parse_xml_metadata(zf)

            except zipfile.BadZipFile:
                item['error'] = 'Bozuk veya şifreli HWT arşivi'
            except Exception as e:
                item['error'] = f"Ayrıştırma hatası: {str(e)}"

            items.append(item)

    items.sort(key=lambda x: x['name'].lower())
    return items

@app.route('/api/watchfaces')
def api_watchfaces():
    data = scan_hwt_files()
    return jsonify({
        'success': True,
        'count': len(data),
        'data': data
    })

@app.route('/preview/<path:filename>')
def serve_preview(filename):
    cache_path = os.path.join(CACHE_DIR, filename)
    if os.path.exists(cache_path):
        return send_file(cache_path)
    abort(404)

@app.route('/download/<path:filename>')
def download_hwt(filename):
    for hwt_dir in HWT_DIRS:
        full_path = os.path.join(hwt_dir, filename)
        if os.path.exists(full_path):
            return send_file(full_path, as_attachment=True)
    abort(404)

# ─── LÜKS BEYAZ VE ALTIN TEMALI FRONTEND ───
INDEX_HTML = """<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>HUAWEI HWT KADRAN KOLEKSİYONU</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@500;600;700;800&family=Montserrat:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-body: #FDFCF9;
            --bg-card: #FFFFFF;
            --gold-100: #FBF8EE;
            --gold-200: #F3E5BE;
            --gold-300: #DEBF6C;
            --gold-400: #C59E3F;
            --gold-500: #A88126;
            --gold-gradient: linear-gradient(135deg, #F3DE9A 0%, #D8B756 50%, #A47D22 100%);
            --gold-text-gradient: linear-gradient(135deg, #8E6D18 0%, #C49C3A 50%, #8A6510 100%);
            --gold-glow: 0 10px 30px rgba(216, 183, 86, 0.22);
            --gold-border: rgba(216, 183, 86, 0.4);
            --text-main: #1C1A17;
            --text-muted: #7A756C;
            --border-subtle: rgba(220, 205, 175, 0.35);
            --radius-xl: 22px;
            --radius-lg: 14px;
            --radius-md: 8px;
            --transition: all 0.35s cubic-bezier(0.16, 1, 0.3, 1);
        }

        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            background-color: var(--bg-body);
            color: var(--text-main);
            font-family: 'Montserrat', sans-serif;
            min-height: 100vh;
            overflow-x: hidden;
            background-image: 
                radial-gradient(circle at 12% 18%, rgba(238, 219, 154, 0.14) 0%, transparent 40%),
                radial-gradient(circle at 88% 82%, rgba(216, 183, 86, 0.12) 0%, transparent 45%);
        }

        body::before {
            content: '';
            position: fixed;
            top: 0; left: 0; right: 0; bottom: 0;
            background-image: radial-gradient(circle, rgba(194, 155, 56, 0.08) 1.2px, transparent 1.2px);
            background-size: 34px 34px;
            pointer-events: none;
            z-index: 0;
        }

        /* ─── HEADER ─── */
        header {
            position: sticky;
            top: 0;
            z-index: 100;
            background: rgba(255, 255, 255, 0.92);
            backdrop-filter: blur(20px);
            -webkit-backdrop-filter: blur(20px);
            border-bottom: 1px solid var(--border-subtle);
            box-shadow: 0 4px 25px rgba(168, 129, 38, 0.05);
        }

        .header-content {
            max-width: 1520px;
            margin: 0 auto;
            padding: 18px 40px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 20px;
        }

        .brand {
            display: flex;
            align-items: center;
            gap: 16px;
            text-decoration: none;
        }

        .brand-emblem {
            width: 48px;
            height: 48px;
            border-radius: 50%;
            background: var(--gold-gradient);
            display: flex;
            align-items: center;
            justify-content: center;
            box-shadow: var(--gold-glow);
            border: 2px solid #FFF;
        }

        .brand-emblem svg {
            width: 26px;
            height: 26px;
            fill: #FFFFFF;
        }

        .brand-info h1 {
            font-family: 'Cinzel', serif;
            font-size: 24px;
            font-weight: 700;
            letter-spacing: 2px;
            background: var(--gold-text-gradient);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }

        .brand-info span {
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 2.5px;
            color: var(--text-muted);
            display: block;
            margin-top: 1px;
        }

        /* Sağ üst Arama ve Filtreleme */
        .header-actions {
            display: flex;
            align-items: center;
            gap: 14px;
        }

        .search-container {
            position: relative;
            width: 330px;
        }

        .search-input {
            width: 100%;
            height: 46px;
            padding: 0 18px 0 44px;
            border: 1.5px solid var(--gold-border);
            border-radius: 30px;
            background: #FFFFFF;
            font-family: 'Montserrat', sans-serif;
            font-size: 14px;
            color: var(--text-main);
            outline: none;
            transition: var(--transition);
            box-shadow: 0 2px 10px rgba(0,0,0,0.02);
        }

        .search-input:focus {
            border-color: var(--gold-400);
            box-shadow: 0 0 0 4px rgba(216, 183, 86, 0.18);
        }

        .search-icon {
            position: absolute;
            left: 16px;
            top: 50%;
            transform: translateY(-50%);
            width: 18px;
            height: 18px;
            stroke: var(--gold-400);
            pointer-events: none;
        }

        .filter-select {
            height: 46px;
            padding: 0 36px 0 18px;
            border: 1.5px solid var(--gold-border);
            border-radius: 30px;
            background: #FFFFFF;
            font-family: 'Montserrat', sans-serif;
            font-size: 13px;
            font-weight: 500;
            color: var(--text-main);
            outline: none;
            cursor: pointer;
            transition: var(--transition);
            appearance: none;
            background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='14' height='14' viewBox='0 0 24 24' fill='none' stroke='%23C29B38' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpolyline points='6 9 12 15 18 9'%3E%3C/polyline%3E%3C/svg%3E");
            background-repeat: no-repeat;
            background-position: right 14px center;
        }

        .filter-select:focus {
            border-color: var(--gold-400);
        }

        .order-btn {
            width: 46px;
            height: 46px;
            border-radius: 50%;
            border: 1.5px solid var(--gold-border);
            background: #FFFFFF;
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            transition: var(--transition);
            color: var(--gold-400);
        }

        .order-btn:hover {
            background: var(--gold-100);
            border-color: var(--gold-400);
            transform: translateY(-2px);
        }

        /* ─── İSTATİSTİK ŞERİDİ ─── */
        .stats-bar {
            max-width: 1520px;
            margin: 24px auto 0;
            padding: 0 40px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            position: relative;
            z-index: 1;
        }

        .stats-count {
            display: inline-flex;
            align-items: center;
            gap: 10px;
            background: #FFFFFF;
            border: 1px solid var(--gold-border);
            padding: 8px 20px;
            border-radius: 30px;
            box-shadow: 0 2px 10px rgba(216, 183, 86, 0.08);
            font-size: 13px;
            color: var(--text-muted);
        }

        .stats-count strong {
            color: var(--gold-500);
            font-weight: 700;
            font-size: 15px;
        }

        .refresh-btn {
            background: #FFFFFF;
            border: 1px solid var(--gold-border);
            color: var(--gold-500);
            padding: 8px 20px;
            border-radius: 30px;
            font-size: 13px;
            font-weight: 600;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 8px;
            transition: var(--transition);
        }

        .refresh-btn:hover {
            background: var(--gold-gradient);
            color: #FFFFFF;
            border-color: transparent;
            box-shadow: var(--gold-glow);
        }

        /* ─── KARTLAR ALANI ─── */
        main {
            max-width: 1520px;
            margin: 24px auto 80px;
            padding: 0 40px;
            position: relative;
            z-index: 1;
        }

        .cards-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(310px, 1fr));
            gap: 32px;
        }

        /* KADRAN KARTI */
        .watch-card {
            background: var(--bg-card);
            border-radius: var(--radius-xl);
            border: 1px solid var(--border-subtle);
            box-shadow: 0 8px 26px rgba(0, 0, 0, 0.03);
            overflow: hidden;
            display: flex;
            flex-direction: column;
            cursor: pointer;
            transition: var(--transition);
            position: relative;
        }

        .watch-card::before {
            content: '';
            position: absolute;
            inset: 0;
            border-radius: var(--radius-xl);
            padding: 1.5px;
            background: linear-gradient(135deg, rgba(238, 219, 154, 0.9), rgba(216, 183, 86, 0.25), rgba(255, 255, 255, 0));
            -webkit-mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
            mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
            -webkit-mask-composite: xor;
            mask-composite: exclude;
            pointer-events: none;
            transition: var(--transition);
        }

        .watch-card:hover {
            transform: translateY(-8px);
            box-shadow: 0 22px 45px rgba(168, 129, 38, 0.14), var(--gold-glow);
            border-color: var(--gold-300);
        }

        .watch-preview-wrapper {
            position: relative;
            width: 100%;
            height: 310px;
            background: radial-gradient(circle, #FFFFFF 0%, #F8F5EE 100%);
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 24px;
            border-bottom: 1px solid var(--border-subtle);
            overflow: hidden;
        }

        /* Altın Çerçeveli Saat Kasası */
        .watch-frame {
            position: relative;
            width: 236px;
            height: 236px;
            border-radius: 50%;
            padding: 7px;
            background: linear-gradient(135deg, #FDF9EE 0%, #DEBF6C 45%, #8E6D18 100%);
            box-shadow: 0 12px 30px rgba(168, 129, 38, 0.24);
            transition: var(--transition);
            display: flex;
            align-items: center;
            justify-content: center;
        }

        .watch-card:hover .watch-frame {
            transform: scale(1.05);
            box-shadow: 0 18px 40px rgba(216, 183, 86, 0.38);
        }

        /* Saat Kadranı İçi */
        .watch-frame-inner {
            width: 100%;
            height: 100%;
            border-radius: 50%;
            background: radial-gradient(circle, #2A2825 0%, #151412 100%);
            overflow: hidden;
            display: flex;
            align-items: center;
            justify-content: center;
            position: relative;
            box-shadow: inset 0 0 15px rgba(0,0,0,0.6);
        }

        .watch-frame-inner img {
            width: 100%;
            height: 100%;
            object-fit: cover;
            border-radius: 50%;
            transition: var(--transition);
        }

        .aod-badge {
            position: absolute;
            top: 14px;
            right: 14px;
            background: rgba(28, 26, 23, 0.85);
            color: var(--gold-200);
            border: 1px solid var(--gold-border);
            font-size: 10px;
            font-weight: 700;
            letter-spacing: 1px;
            padding: 4px 10px;
            border-radius: 20px;
            backdrop-filter: blur(8px);
            z-index: 5;
        }

        .no-preview-icon {
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 10px;
            color: var(--gold-400);
        }

        .no-preview-icon svg {
            width: 48px;
            height: 48px;
            stroke: var(--gold-300);
        }

        .no-preview-icon span {
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 1.5px;
            color: var(--text-muted);
        }

        .watch-details {
            padding: 22px;
            display: flex;
            flex-direction: column;
            flex: 1;
        }

        .watch-name {
            font-family: 'Cinzel', serif;
            font-size: 17px;
            font-weight: 700;
            color: var(--text-main);
            margin-bottom: 8px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            letter-spacing: 0.5px;
        }

        .watch-meta-tags {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            margin-bottom: 16px;
        }

        .meta-tag {
            font-size: 11px;
            font-weight: 500;
            padding: 4px 10px;
            border-radius: 20px;
            background: var(--gold-100);
            color: var(--gold-500);
            border: 1px solid rgba(216, 183, 86, 0.25);
            display: inline-flex;
            align-items: center;
            gap: 4px;
        }

        .watch-divider {
            height: 1px;
            background: linear-gradient(90deg, transparent, var(--border-subtle), transparent);
            margin: auto 0 16px 0;
        }

        .card-actions {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 10px;
        }

        .btn-inspect {
            flex: 1;
            height: 40px;
            border-radius: 20px;
            background: #FFFFFF;
            border: 1.5px solid var(--gold-border);
            color: var(--gold-500);
            font-family: 'Montserrat', sans-serif;
            font-size: 12px;
            font-weight: 600;
            letter-spacing: 0.5px;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 6px;
            cursor: pointer;
            transition: var(--transition);
        }

        .watch-card:hover .btn-inspect {
            background: var(--gold-gradient);
            color: #FFFFFF;
            border-color: transparent;
            box-shadow: 0 4px 15px rgba(216, 183, 86, 0.3);
        }

        .btn-download-icon {
            width: 40px;
            height: 40px;
            border-radius: 50%;
            background: var(--gold-100);
            border: 1px solid var(--gold-border);
            color: var(--gold-500);
            display: flex;
            align-items: center;
            justify-content: center;
            text-decoration: none;
            transition: var(--transition);
            flex-shrink: 0;
        }

        .btn-download-icon:hover {
            background: var(--gold-400);
            color: #FFFFFF;
            transform: scale(1.08);
        }

        /* ─── BOŞ / YÜKLENİYOR DURUMU ─── */
        .state-container {
            grid-column: 1 / -1;
            text-align: center;
            padding: 80px 20px;
            background: #FFFFFF;
            border-radius: var(--radius-xl);
            border: 1px dashed var(--gold-border);
        }

        .state-container svg {
            width: 64px;
            height: 64px;
            stroke: var(--gold-300);
            margin-bottom: 16px;
        }

        .state-container h3 {
            font-family: 'Cinzel', serif;
            font-size: 22px;
            color: var(--text-main);
            margin-bottom: 8px;
        }

        .state-container p {
            color: var(--text-muted);
            font-size: 14px;
            max-width: 460px;
            margin: 0 auto 20px;
        }

        .folder-hint {
            display: inline-block;
            background: var(--gold-100);
            padding: 8px 16px;
            border-radius: 8px;
            font-family: monospace;
            font-size: 13px;
            color: var(--gold-500);
            border: 1px solid rgba(216, 183, 86, 0.3);
        }

        /* ─── DETAY MODALI ─── */
        .modal-backdrop {
            position: fixed;
            inset: 0;
            background: rgba(28, 26, 23, 0.7);
            backdrop-filter: blur(14px);
            z-index: 999;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 24px;
            opacity: 0;
            visibility: hidden;
            transition: var(--transition);
        }

        .modal-backdrop.active {
            opacity: 1;
            visibility: visible;
        }

        .modal-window {
            background: #FFFFFF;
            border-radius: 26px;
            max-width: 880px;
            width: 100%;
            max-height: 90vh;
            overflow-y: auto;
            border: 1.5px solid var(--gold-border);
            box-shadow: 0 25px 60px rgba(0, 0, 0, 0.28);
            transform: scale(0.92) translateY(20px);
            transition: var(--transition);
            display: flex;
            flex-direction: column;
        }

        .modal-backdrop.active .modal-window {
            transform: scale(1) translateY(0);
        }

        .modal-header {
            padding: 24px 32px;
            border-bottom: 1px solid var(--border-subtle);
            display: flex;
            align-items: center;
            justify-content: space-between;
            position: sticky;
            top: 0;
            background: #FFFFFF;
            z-index: 10;
        }

        .modal-title h2 {
            font-family: 'Cinzel', serif;
            font-size: 22px;
            font-weight: 700;
            background: var(--gold-text-gradient);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }

        .modal-close {
            width: 40px;
            height: 40px;
            border-radius: 50%;
            border: 1px solid var(--gold-border);
            background: #FFF;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            color: var(--gold-500);
            transition: var(--transition);
        }

        .modal-close:hover {
            background: var(--gold-400);
            color: #FFF;
            transform: rotate(90deg);
        }

        .modal-body {
            padding: 32px;
        }

        .modal-hero {
            display: flex;
            gap: 32px;
            margin-bottom: 30px;
            align-items: center;
        }

        .modal-watch-visual-box {
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 12px;
            flex-shrink: 0;
        }

        .modal-watch-visual {
            width: 260px;
            height: 260px;
            border-radius: 50%;
            padding: 8px;
            background: linear-gradient(135deg, #FDF9EE 0%, #D8B756 50%, #8E6D18 100%);
            box-shadow: 0 15px 35px rgba(168, 129, 38, 0.28);
        }

        .modal-watch-visual-inner {
            width: 100%;
            height: 100%;
            border-radius: 50%;
            background: radial-gradient(circle, #2A2825 0%, #151412 100%);
            overflow: hidden;
            display: flex;
            align-items: center;
            justify-content: center;
            box-shadow: inset 0 0 15px rgba(0,0,0,0.6);
        }

        .modal-watch-visual-inner img {
            width: 100%;
            height: 100%;
            object-fit: cover;
            border-radius: 50%;
        }

        .view-switch-btns {
            display: flex;
            gap: 8px;
        }

        .btn-view-toggle {
            padding: 6px 14px;
            border-radius: 20px;
            border: 1px solid var(--gold-border);
            background: #FFFFFF;
            color: var(--gold-500);
            font-size: 11px;
            font-weight: 600;
            cursor: pointer;
            transition: var(--transition);
        }

        .btn-view-toggle.active {
            background: var(--gold-gradient);
            color: #FFFFFF;
            border-color: transparent;
        }

        .modal-quick-info {
            flex: 1;
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 16px;
        }

        .quick-box {
            padding: 14px 18px;
            border-radius: 14px;
            background: var(--gold-100);
            border: 1px solid rgba(216, 183, 86, 0.2);
        }

        .quick-label {
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 1px;
            color: var(--text-muted);
            margin-bottom: 4px;
        }

        .quick-val {
            font-size: 15px;
            font-weight: 600;
            color: var(--text-main);
        }

        .modal-section-title {
            font-family: 'Cinzel', serif;
            font-size: 16px;
            font-weight: 700;
            color: var(--gold-500);
            margin: 24px 0 12px;
            padding-bottom: 8px;
            border-bottom: 1.5px solid var(--border-subtle);
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .xml-table {
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
            background: #FAF8F5;
            border-radius: 12px;
            overflow: hidden;
            border: 1px solid var(--border-subtle);
        }

        .xml-table th, .xml-table td {
            padding: 12px 18px;
            text-align: left;
            border-bottom: 1px solid rgba(216, 183, 86, 0.1);
        }

        .xml-table th {
            background: #F3ECE0;
            color: var(--gold-500);
            font-weight: 600;
            width: 35%;
        }

        .archive-list {
            max-height: 180px;
            overflow-y: auto;
            background: #FAF8F5;
            border: 1px solid var(--border-subtle);
            border-radius: 12px;
            padding: 12px 16px;
            font-family: monospace;
            font-size: 12px;
            color: var(--text-muted);
        }

        .archive-file-item {
            padding: 4px 0;
            border-bottom: 1px dashed rgba(216, 183, 86, 0.15);
        }

        /* ─── FOOTER ─── */
        footer {
            border-top: 1px solid var(--border-subtle);
            padding: 30px 40px;
            text-align: center;
            background: #FFFFFF;
            position: relative;
            z-index: 1;
        }

        footer p {
            font-size: 13px;
            color: var(--text-muted);
            letter-spacing: 0.5px;
        }

        footer span {
            color: var(--gold-400);
            font-weight: 600;
        }

        @media (max-width: 860px) {
            .header-content {
                flex-direction: column;
                align-items: stretch;
            }
            .header-actions {
                flex-wrap: wrap;
            }
            .search-container {
                width: 100%;
            }
            .modal-hero {
                flex-direction: column;
            }
            .modal-quick-info {
                grid-template-columns: 1fr;
                width: 100%;
            }
        }
    </style>
</head>
<body>

    <!-- ÜST BİLGİ VE ARAMA ÇUBUĞU -->
    <header>
        <div class="header-content">
            <a href="#" class="brand">
                <div class="brand-emblem">
                    <svg viewBox="0 0 24 24"><path d="M12 2C6.5 2 2 6.5 2 12s4.5 10 10 10 10-4.5 10-10S17.5 2 12 2zm4.2 14.2L11 13V7h1.5v5.2l4.5 2.7-.8 1.3z"/></svg>
                </div>
                <div class="brand-info">
                    <h1>HWT LISTER</h1>
                    <span>Huawei Watch Face Haute Horlogerie</span>
                </div>
            </a>

            <!-- Sağ üst Arama ve Filtreleme -->
            <div class="header-actions">
                <div class="search-container">
                    <svg class="search-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
                    <input type="text" id="searchInput" class="search-input" placeholder="Kadran veya yazar ara...">
                </div>

                <select id="sortSelect" class="filter-select">
                    <option value="name">İsme Göre Sırala</option>
                    <option value="size">Boyuta Göre Sırala</option>
                    <option value="date">Tarihe Göre Sırala</option>
                    <option value="images">Görsel Sayısına Göre</option>
                </select>

                <button id="orderBtn" class="order-btn" title="Sıralama Yönünü Değiştir">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="7 10 12 15 17 10"/></svg>
                </button>
            </div>
        </div>
    </header>

    <!-- İSTATİSTİK ŞERİDİ -->
    <div class="stats-bar">
        <div class="stats-count">
            Koleksiyon: <strong id="totalCount">0</strong> Kadran
        </div>
        <button class="refresh-btn" onclick="loadWatchfaces()">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 4v6h-6"/><path d="M1 20v-6h6"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>
            Yenile
        </button>
    </div>

    <!-- ANA KARTLAR ALANI -->
    <main>
        <div id="cardsGrid" class="cards-grid"></div>
    </main>

    <!-- DETAY MODALI -->
    <div id="modalBackdrop" class="modal-backdrop">
        <div class="modal-window">
            <div class="modal-header">
                <div class="modal-title">
                    <h2 id="modalWatchTitle">Kadran Detayları</h2>
                </div>
                <button class="modal-close" onclick="closeModal()">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                </button>
            </div>
            <div class="modal-body" id="modalBody"></div>
        </div>
    </div>

    <!-- ALT BİLGİ -->
    <footer>
        <p>HUAWEI HWT KADRAN GALERİSİ &copy; 2026 — <span>Lüks & Zarafet</span></p>
    </footer>

    <script>
        let allData = [];
        let sortField = 'name';
        let sortAsc = true;
        let searchQuery = '';

        function fallbackPlaceholder(imgElement) {
            imgElement.parentElement.innerHTML = `
                <div class="no-preview-icon">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
                    <span>Önizleme Yok</span>
                </div>
            `;
        }

        async function loadWatchfaces() {
            const grid = document.getElementById('cardsGrid');
            grid.innerHTML = `
                <div class="state-container">
                    <h3>Kadranlar Taranıyor...</h3>
                    <p>HWT arşivleri ve görsel kapakları hazırlanıyor.</p>
                </div>
            `;

            try {
                const res = await fetch('/api/watchfaces');
                const json = await res.json();
                if (json.success) {
                    allData = json.data;
                    renderCards();
                } else {
                    showError(json.error || 'Kadranlar listelenirken bir sorun oluştu.');
                }
            } catch (err) {
                showError('Sunucu bağlantısı sağlanamadı: ' + err.message);
            }
        }

        function renderCards() {
            const grid = document.getElementById('cardsGrid');
            let filtered = allData.filter(item => {
                if (!searchQuery) return true;
                const q = searchQuery.toLowerCase();
                const inName = item.name.toLowerCase().includes(q);
                const inXml = JSON.stringify(item.xml_data).toLowerCase().includes(q);
                return inName || inXml;
            });

            filtered.sort((a, b) => {
                let valA = a[sortField];
                let valB = b[sortField];
                if (sortField === 'size') {
                    valA = a.size_bytes;
                    valB = b.size_bytes;
                } else if (sortField === 'images') {
                    valA = a.image_count;
                    valB = b.image_count;
                }

                if (typeof valA === 'string') {
                    return sortAsc ? valA.localeCompare(valB, 'tr') : valB.localeCompare(valA, 'tr');
                }
                return sortAsc ? valA - valB : valB - valA;
            });

            document.getElementById('totalCount').innerText = filtered.length;

            if (filtered.length === 0) {
                if (allData.length === 0) {
                    grid.innerHTML = `
                        <div class="state-container">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
                            <h3>Henüz Kadran Bulunamadı</h3>
                            <p>Lütfen kadranlarınızı aşağıdaki klasörün içine kopyalayın:</p>
                            <span class="folder-hint">hwt/ veya .hwt/</span>
                        </div>
                    `;
                } else {
                    grid.innerHTML = `
                        <div class="state-container">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
                            <h3>Eşleşen Kadran Yok</h3>
                            <p>"${escapeHtml(searchQuery)}" aramasıyla eşleşen bir sonuç bulunamadı.</p>
                        </div>
                    `;
                }
                return;
            }

            let html = '';
            filtered.forEach((item) => {
                const previewHtml = item.has_preview 
                    ? `<img src="${item.preview_url}" alt="${escapeHtml(item.name)}" loading="lazy" onerror="fallbackPlaceholder(this)">`
                    : `<div class="no-preview-icon">
                         <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
                         <span>Önizleme Yok</span>
                       </div>`;

                const aodBadge = item.has_aod ? `<span class="aod-badge">AOD MEVCUT</span>` : '';

                html += `
                    <div class="watch-card" onclick="openModal('${escapeHtml(item.id)}')">
                        <div class="watch-preview-wrapper">
                            ${aodBadge}
                            <div class="watch-frame">
                                <div class="watch-frame-inner">
                                    ${previewHtml}
                                </div>
                            </div>
                        </div>
                        <div class="watch-details">
                            <div class="watch-name" title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</div>
                            <div class="watch-meta-tags">
                                <span class="meta-tag">
                                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="12" height="12"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                                    ${item.size}
                                </span>
                                <span class="meta-tag">
                                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="12" height="12"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>
                                    ${item.image_count} Görsel
                                </span>
                                <span class="meta-tag">
                                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="12" height="12"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
                                    ${item.modified.split(' ')[0]}
                                </span>
                            </div>
                            <div class="watch-divider"></div>
                            <div class="card-actions">
                                <button class="btn-inspect" onclick="event.stopPropagation(); openModal('${escapeHtml(item.id)}')">
                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
                                    Detayları İncele
                                </button>
                                <a href="/download/${encodeURIComponent(item.filename)}" class="btn-download-icon" title="HWT Dosyasını İndir" onclick="event.stopPropagation();">
                                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                                </a>
                            </div>
                        </div>
                    </div>
                `;
            });
            grid.innerHTML = html;
        }

        function switchModalImage(imgUrl, btn) {
            const img = document.getElementById('modalMainImage');
            if (img) img.src = imgUrl;
            document.querySelectorAll('.btn-view-toggle').forEach(b => b.classList.remove('active'));
            if (btn) btn.classList.add('active');
        }

        function openModal(id) {
            const item = allData.find(x => x.id === id);
            if (!item) return;

            document.getElementById('modalWatchTitle').innerText = item.name;

            const previewSrc = item.has_preview ? item.preview_url : '';
            const previewHtml = item.has_preview 
                ? `<img id="modalMainImage" src="${item.preview_url}" alt="${escapeHtml(item.name)}" onerror="fallbackPlaceholder(this)">`
                : `<div class="no-preview-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg><span>Önizleme Yok</span></div>`;

            let toggleButtons = '';
            if (item.has_preview && item.has_aod) {
                toggleButtons = `
                    <div class="view-switch-btns">
                        <button class="btn-view-toggle active" onclick="switchModalImage('${item.preview_url}', this)">Ana Kapak</button>
                        <button class="btn-view-toggle" onclick="switchModalImage('${item.aod_url}', this)">AOD (Bekleme Modu)</button>
                    </div>
                `;
            }

            let xmlRows = '';
            if (item.xml_data && Object.keys(item.xml_data).length > 0) {
                for (const [file, data] of Object.entries(item.xml_data)) {
                    for (const [k, v] of Object.entries(data)) {
                        xmlRows += `<tr><th>${escapeHtml(k.toUpperCase())}</th><td>${escapeHtml(v)}</td></tr>`;
                    }
                }
            }

            let fileListHtml = '';
            if (item.file_list && item.file_list.length > 0) {
                fileListHtml = item.file_list.map(f => `<div class="archive-file-item">${escapeHtml(f)}</div>`).join('');
            }

            document.getElementById('modalBody').innerHTML = `
                <div class="modal-hero">
                    <div class="modal-watch-visual-box">
                        <div class="modal-watch-visual">
                            <div class="modal-watch-visual-inner">
                                ${previewHtml}
                            </div>
                        </div>
                        ${toggleButtons}
                    </div>
                    <div class="modal-quick-info">
                        <div class="quick-box">
                            <div class="quick-label">Dosya Adı</div>
                            <div class="quick-val">${escapeHtml(item.filename)}</div>
                        </div>
                        <div class="quick-box">
                            <div class="quick-label">Dosya Boyutu</div>
                            <div class="quick-val">${item.size}</div>
                        </div>
                        <div class="quick-box">
                            <div class="quick-label">Son Değişiklik</div>
                            <div class="quick-val">${item.modified}</div>
                        </div>
                        <div class="quick-box">
                            <div class="quick-label">İçerik Sayısı</div>
                            <div class="quick-val">${item.image_count} Görsel / ${item.file_count} Dosya</div>
                        </div>
                    </div>
                </div>

                ${xmlRows ? `
                    <div class="modal-section-title">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
                        Kadran Yapılandırma Bilgileri (XML)
                    </div>
                    <table class="xml-table">
                        <tbody>${xmlRows}</tbody>
                    </table>
                ` : ''}

                <div class="modal-section-title">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/></svg>
                    Arşiv İçeriği (İlk ${item.file_list.length} Dosya)
                </div>
                <div class="archive-list">
                    ${fileListHtml}
                </div>
            `;

            document.getElementById('modalBackdrop').classList.add('active');
            document.body.style.overflow = 'hidden';
        }

        function closeModal() {
            document.getElementById('modalBackdrop').classList.remove('active');
            document.body.style.overflow = '';
        }

        document.getElementById('modalBackdrop').addEventListener('click', (e) => {
            if (e.target.id === 'modalBackdrop') closeModal();
        });

        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') closeModal();
        });

        function showError(msg) {
            document.getElementById('cardsGrid').innerHTML = `
                <div class="state-container" style="border-color: #E74C3C;">
                    <h3 style="color: #C0392B;">Hata Meydana Geldi</h3>
                    <p>${escapeHtml(msg)}</p>
                </div>
            `;
        }

        function escapeHtml(str) {
            if (!str) return '';
            return String(str)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#39;');
        }

        document.getElementById('searchInput').addEventListener('input', (e) => {
            searchQuery = e.target.value.trim();
            renderCards();
        });

        document.getElementById('sortSelect').addEventListener('change', (e) => {
            sortField = e.target.value;
            renderCards();
        });

        document.getElementById('orderBtn').addEventListener('click', () => {
            sortAsc = !sortAsc;
            document.getElementById('orderBtn').innerHTML = sortAsc
                ? `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="7 10 12 15 17 10"/></svg>`
                : `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="7 14 12 9 17 14"/></svg>`;
            renderCards();
        });

        loadWatchfaces();
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(INDEX_HTML)

def open_browser():
    webbrowser.open_new('http://localhost:5000')

if __name__ == '__main__':
    print("=" * 60)
    print("  HUAWEI HWT KADRAN KOLEKSIYONU SUNUCUSU BASLATILDI")
    print("  Adres: http://localhost:5000")
    print("  HWT Dosyalarınızı 'hwt' veya '.hwt' klasörüne atabilirsiniz.")
    print("=" * 60)
    
    Timer(1.2, open_browser).start()
    app.run(host='0.0.0.0', port=5000, debug=False)
