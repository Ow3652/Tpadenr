# Tpadenr — ชั้นแผนที่ Enroute Chart สำหรับ Tpad Helicopter Planner

แปลง Enroute Chart (ICAO) ของ AIP ประเทศไทย (PDF เวกเตอร์) เป็นแผ่นภาพ XYZ แล้วเผยแพร่ขึ้น GitHub Pages
ให้แอป [Tpad Helicopter Planner](https://ow3652.github.io/Tpadhelicopterplanner/) ใช้เป็นชั้นแผนที่ "ENR Chart"

- หน้าเว็บ: https://ow3652.github.io/Tpadenr/  · แผ่นภาพ `tiles/{z}/{x}/{y}.png` (พื้นโปร่งใส) · ข้อมูลฉบับ `meta.json`
- **ใช้ประกอบการวางแผนเท่านั้น ไม่ใช่ชาร์ตทางการ** ต้องตรวจกับ AIP ฉบับที่มีผลทุกครั้ง

## วิธีทำงาน (`build_enr.py`)
1. อ่านเส้นพิกัดสีเทาในไฟล์ PDF โดยตรง → จุดตัดทุก 1° เป็นจุดอ้างอิง (ฉบับ AIRAC 11/26 ได้ 135 จุด)
2. ตรวจคุณภาพ: จุดอ้างอิงต้องมี ≥ 80 จุด ไม่ซ้ำ และสอดคล้องกัน (คลาดจากพหุนามไม่เกิน 0.02°) และต้องอ่าน AIRAC/วันที่มีผลได้ — ไม่ผ่าน = หยุด ไม่เผยแพร่
3. พื้นขาว → โปร่งใส · `gdalwarp -tps` เป็น EPSG:3857 · `gdal2tiles --xyz` ซูม 5–11

## อัปเดตเมื่อมี AIRAC ใหม่ (ทุก 28 วัน)
1. ลบ PDF ฉบับเก่าในโฟลเดอร์ `enr/` แล้วอัปโหลดฉบับใหม่ (ต้องมีไฟล์เดียว) — หรือส่งไฟล์ให้ Claude ทำให้
2. GitHub Actions "Build ENR tiles" จะทำงานเองและเผยแพร่ภายในไม่กี่นาที (ดูผลที่แท็บ Actions)
3. เปิดแอปออนไลน์ ชั้น ENR Chart จะแสดงฉบับใหม่ (มุมจอบอก AIRAC / วันที่มีผล)

ตั้งค่าครั้งแรก: Settings → Pages → Source = **GitHub Actions**
