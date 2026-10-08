<div align="center">

# ⌚ HWT Lister — Huawei Watch Face Gallery

**Huawei akıllı saatler için `.hwt` uzantılı özel kadranları listeleyen, önizleyen, filtreleyen ve detaylarını sunan lüks beyaz & altın temalı web uygulaması.**

[![Python Version](https://img.shields.io/badge/python-3.8+-gold.svg)](https://www.python.org/)
[![Framework](https://img.shields.io/badge/framework-Flask-C8A951.svg)](https://flask.palletsprojects.com/)
[![License](https://img.shields.io/badge/license-MIT-D4AF37.svg)](LICENSE)

</div>

---

## ✨ Özellikler

- **🏛️ Tek Dosya Kolaylığı (`app.py`):** Frontend ve Backend tek bir Flask dosyasında birleştirilmiştir. Kurulum veya ayrı sunucu çalıştırma derdi yoktur. Tek komutla başlar ve tarayıcıyı otomatik açar.
- **💎 Lüks Beyaz & Altın Tasarım:** Patek Philippe, Rolex ve Cartier zarafetinden ilham alan beyaz ve altın kaplama saat kasası tasarımı, cam (glassmorphism) efektleri ve `Cinzel` & `Montserrat` tipografisi.
- **🔍 Gerçek Zamanlı Arama & Akıllı Filtreleme:** Kadran adına, tasarımcıya veya XML içeriğine göre anında canlı arama. İsme, dosya boyutuna, tarihe ve görsel sayısına göre artan/azalan sıralama.
- **🖼️ Akıllı Önizleme & Kapak Ayrıştırma:**
  - HWT arşivleri (ZIP) içerisindeki `preview/cover.jpg`, `cover.png` veya `icon_small.jpg` vitrin görsellerini otomatik tespit eder.
  - AMOLED pil koruması nedeniyle simsiyah olan **AOD (Always-On Display)** ekranlarının yanlışlıkla ana kapak olarak seçilmesini engeller.
  - AOD bekleme modu mevcutsa bunu ayrı bir rozet ve detay sekmesi olarak sunar.
  - Görselleri otomatik olarak web standartlarında RGB formatına dönüştürüp `cache/` klasöründe önbelleğe alır.
- **📊 Derinlemesine XML & Metadata Analizi:** Kadranın XML yapılandırma dosyalarından yazar, çözünürlük, sürüm, desteklenen cihazlar ve arşiv dosya listesini okur.
- **📥 Tek Tıkla HWT İndirme:** Listelenen kadranları doğrudan web arayüzünden saatinize yüklemek üzere indirebilirsiniz.

---

## 🚀 Hızlı Başlangıç

### 1. Gereksinimler

- Python 3.8 veya üzeri
- `flask` ve `Pillow`

Gerekli paketleri yükleyin:

```bash
pip install -r requirements.txt
```

### 2. Kadranları Ekleyin

İndirdiğiniz veya hazırladığınız `.hwt` uzantılı kadran dosyalarını `hwt/` klasörünün içine bırakın:

```
HWT Lister/
│
├── hwt/                       <-- .hwt dosyalarınızı buraya atın
│   ├── RolexDaytona.hwt
│   ├── CasioVintage.hwt
│   └── ...
├── app.py
├── requirements.txt
└── README.md
```

### 3. Çalıştırın

```bash
python app.py
```

Uygulama başladığında tarayıcınız otomatik olarak `http://localhost:5000` adresinde açılacaktır.

---

## 🛠️ Nasıl Çalışır?

1. **HWT Formatı Nedir?** Huawei saat kadranları (`.hwt`), içerisinde XML yapılandırma dosyaları, saat ibreleri, arka planlar ve önizleme görselleri barındıran özel bir ZIP konteyneridir.
2. `app.py`, `hwt/` ve `.hwt/` dizinlerini tarar.
3. Her arşivin içindeki kapak görselini akıllı önceliklendirme algoritmasıyla çıkarıp `cache/` klasörüne kaydeder.
4. `description.xml` ve `watch_face_config.xml` dosyalarını ayrıştırıp kadran adı, tasarımcı, sürüm gibi bilgileri JSON API üzerinden ön yüze iletir.
5. Kullanıcı arayüzü hiçbir harici CSS/JS bağımlılığı olmadan saf modern web teknolojileriyle çalışır.

---

## 📦 Lisans

Bu proje MIT lisansı ile lisanslanmıştır. Detaylar için `LICENSE` dosyasına bakabilirsiniz.
