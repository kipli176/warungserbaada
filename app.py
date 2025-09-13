import os
import requests
from functools import wraps

from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo  # Python 3.9+from functools import wraps
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash
)
from dotenv import load_dotenv
from psycopg_pool import ConnectionPool

# =========================
# Config & App init
# =========================
load_dotenv()

class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
    PERMANENT_SESSION_LIFETIME = timedelta(days=7)
    DATABASE_URL = os.getenv("DATABASE_URL")
    TZ = os.getenv("TZ", "Asia/Jakarta")

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config.from_object(Config)
STORE_NAME = "Toko Waserda"
# =========================
# Database (PostgreSQL)
# =========================
# Kita bikin pool sejak awal walau login masih user statis,
# supaya nanti gampang gunakan DB di halaman lain.
db_pool = None
if app.config["DATABASE_URL"]:
    db_pool = ConnectionPool(conninfo=app.config["DATABASE_URL"], max_size=10)

def db_conn():
    """Ambil koneksi dari pool. Gunakan: with db_conn() as conn: ..."""
    if not db_pool:
        raise RuntimeError("DATABASE_URL belum diset. Cek .env")
    return db_pool.connection()

# =========================
# Auth utils (user statis)
# =========================
STATIC_USER = {"username": "admin", "password": "123456"}  # ganti sesuai kebutuhan

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("user"):
            # simpan tujuan agar setelah login bisa diarahkan lagi
            return redirect(url_for("login", next=request.path))
        return fn(*args, **kwargs)
    return wrapper


def rupiah(n: int) -> str:
    # format "Rp 12.345" tanpa desimal
    return "Rp {:,}".format(int(n)).replace(",", ".")

@app.template_filter("rupiah")
def jinja_rupiah(value):
    try:
        return "Rp " + f"{int(value):,}".replace(",", ".")
    except Exception:
        return "Rp 0"

def build_receipt_text(*, sale_date: str, buyer_name: str, items: list, total: int, paid: int, change: int) -> str:
    """
    items: list[ {nama, qty, jual} ] — harga beli tidak dikirim ke pembeli
    """
    # Tanggal & jam lokal
    try:
        now_local = datetime.now(ZoneInfo(app.config.get("TZ", "Asia/Jakarta")))
        ts = now_local.strftime("%Y-%m-%d %H:%M")
    except Exception:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = []
    lines.append(f"*{STORE_NAME}*")
    lines.append("Nota Belanja")
    lines.append(f"Tanggal : {sale_date} {ts[11:]}")
    if buyer_name:
        lines.append(f"Pembeli : {buyer_name}")
    lines.append("--------------------------------")
    # Daftar barang
    for it in items:
        nama = it["nama"]
        qty  = int(it["qty"])
        jual = int(it["jual"])
        subtotal = qty * jual
        # contoh: Indomie Goreng
        #         2 x Rp 3.500 = Rp 7.000
        lines.append(nama)
        lines.append(f"{qty} x {rupiah(jual)} = {rupiah(subtotal)}")
    lines.append("--------------------------------")
    lines.append(f"Total   : *{rupiah(total)}*")
    lines.append(f"Bayar   : {rupiah(paid)}")
    lines.append(f"Kembali : {rupiah(change)}")
    lines.append("--------------------------------")
    lines.append("Terima kasih 🙏")
    return "\n".join(lines)
# =========================
# Routes
# =========================
@app.route("/")
def index():
    if session.get("user"):
        return redirect(url_for("penjualan"))
    return redirect(url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html")

    # POST
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    if username == STATIC_USER["username"] and password == STATIC_USER["password"]:
        session["user"] = {"username": username}
        session.permanent = True
        dest = request.args.get("next") or url_for("penjualan")
        return redirect(dest)
    flash("Username / password salah", "error")
    return render_template("login.html", username=username)

@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/penjualan")
@login_required
def penjualan():
    # sementara hanya placeholder, nanti kita isi HTML Jinja atau render halaman JS/Tailwind
    #return render_template("base.html", page_title="Penjualan", body="<div class='p-4'>Halaman Penjualan (placeholder)</div>")
    buyers = []
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, phone_e164 FROM buyers ORDER BY name ASC LIMIT 500")
            for rid, name, phone in cur.fetchall():
                buyers.append({"id": rid, "name": name, "phone_e164": phone})
    return render_template("penjualan.html", buyers=buyers)

@app.route("/penjualan", methods=["POST"])
@login_required
def penjualan_save():
    """
    Terima JSON:
    {
      "tgl": "YYYY-MM-DD",
      "buyer_id": "<uuid>",
      "items": [{"nama": str, "beli": int, "jual": int, "qty": int}, ...],
      "paid_amount": int
    }
    Simpan ke sales + sale_items dalam satu transaksi.
    """
    data = request.get_json(silent=True) or {}
    tgl = data.get("tgl")
    buyer_id = data.get("buyer_id")
    items = data.get("items") or []
    paid_amount = int(data.get("paid_amount") or 0)

    if not tgl or not buyer_id or not items:
      return {"ok": False, "error": "Data tidak lengkap"}, 400

    # hitung total untuk header (sebenarnya trigger juga akan menghitung, tapi kita isi saja)
    total_amount = sum(int(it["jual"]) * int(it["qty"]) for it in items)
    total_cost   = sum(int(it["beli"]) * int(it["qty"]) for it in items)
    total_profit = total_amount - total_cost
    change_amount = max(0, paid_amount - total_amount)

    try:
        with db_conn() as conn:
            with conn.cursor() as cur:
                # ambil info pembeli dulu (untuk WA)
                cur.execute("SELECT name, phone_e164 FROM buyers WHERE id=%s", (buyer_id,))
                row = cur.fetchone()
                if row:
                    buyer_name, buyer_phone = row[0], row[1]
                wa_status = 'pending' if (buyer_phone and buyer_phone.strip()) else 'none'
                # 1) insert sales (header)
                cur.execute("""
                    INSERT INTO sales
                      (sale_date, buyer_id, total_amount, total_cost, total_profit,
                       paid_amount, change_amount, wa_status)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING id
                """, (tgl, buyer_id, total_amount, total_cost, total_profit, paid_amount, change_amount, wa_status))
                sale_id = cur.fetchone()[0]

                # 2) insert items (detail)
                for it in items:
                    nama = it["nama"]
                    beli = int(it["beli"])
                    jual = int(it["jual"])
                    qty  = int(it["qty"])
                    line_total  = jual * qty
                    line_cost   = beli * qty
                    line_profit = line_total - line_cost

                    cur.execute("""
                        INSERT INTO sale_items
                          (sale_id, item_name, cost_price, sale_price, qty, line_total, line_cost, line_profit)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                    """, (sale_id, nama, beli, jual, qty, line_total, line_cost, line_profit))

                # 3) selesai → commit otomatis (keluar from-with)
#        return {"ok": True, "sale_id": sale_id}
    except Exception as e:
        app.logger.exception("Gagal simpan transaksi")
        return {"ok": False, "error": str(e)}, 500

    # === Kirim WA setelah transaksi disimpan ===
    # Jika tidak ada nomor, cukup kembalikan sukses tanpa kirim.
    if not buyer_phone:
        return {"ok": True, "sale_id": sale_id}

    # Build pesan nota
    message_text = build_receipt_text(
        sale_date=tgl,
        buyer_name=buyer_name or "",
        items=[{"nama": it["nama"], "qty": it["qty"], "jual": it["jual"]} for it in items],
        total=total_amount,
        paid=paid_amount,
        change=change_amount
    )

    # Pastikan nomor dalam format 62xxxxxxxx (API kamu tidak pakai tanda +)
    number = (buyer_phone or "").lstrip("+").strip()

    try:
        resp = requests.post(
            "https://blast.sukipli.work/send-message",
            json={"number": number, "message": message_text},
            timeout=10
        )
        ok = 200 <= resp.status_code < 300
        with db_conn() as conn:
            with conn.cursor() as cur:
                if ok:
                    cur.execute(
                        "UPDATE sales SET wa_status='sent', wa_sent_at=now() WHERE id=%s",
                        (sale_id,)
                    )
                else:
                    cur.execute(
                        "UPDATE sales SET wa_status='failed' WHERE id=%s",
                        (sale_id,)
                    )
        app.logger.info("WA send status=%s body=%s", resp.status_code, resp.text)
    except Exception as e:
        app.logger.warning("WA send failed: %s", e)
        # tandai failed
        try:
            with db_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE sales SET wa_status='failed' WHERE id=%s", (sale_id,))
        except Exception:
            pass

    return {"ok": True, "sale_id": sale_id}

# Hanya jika belum ada
@app.route("/pembeli", methods=["GET", "POST"])
@login_required
def pembeli_page():
#    return render_template("base.html", page_title="Pembeli", body="<div class='p-4'>Halaman Pembeli (sementara)</div>")
    if request.method == "POST":
        # Create buyer
        name = (request.form.get("name") or "").strip()
        phone = (request.form.get("phone_e164") or "").strip() or None
        note  = request.form.get("note") or ""
        wa_opt_in = True if request.form.get("wa_opt_in") else False
        if not name:
            flash("Nama wajib diisi", "error")
            return redirect(url_for("pembeli_page"))
        try:
            with db_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO buyers (name, phone_e164, wa_opt_in, note)
                        VALUES (%s,%s,%s,%s)
                    """, (name, phone, wa_opt_in, note))
            flash("Pembeli ditambahkan", "success")
        except Exception as e:
            app.logger.exception("insert buyer failed")
            flash(f"Gagal simpan: {e}", "error")
        return redirect(url_for("pembeli_page"))

    # GET list (with search)
    q = (request.args.get("q") or "").strip()
    buyers = []
    try:
        with db_conn() as conn:
            with conn.cursor() as cur:
                if q:
                    cur.execute("""
                        SELECT id, name, phone_e164, wa_opt_in, note
                        FROM buyers
                        WHERE (name ILIKE %s OR COALESCE(phone_e164,'') ILIKE %s)
                        ORDER BY name ASC LIMIT 1000
                    """, (f"%{q}%", f"%{q}%"))
                else:
                    cur.execute("""
                        SELECT id, name, phone_e164, wa_opt_in, note
                        FROM buyers
                        ORDER BY created_at DESC LIMIT 1000
                    """)
                for rid, name, phone, wa_opt_in, note in cur.fetchall():
                    buyers.append({"id": rid, "name": name, "phone_e164": phone, "wa_opt_in": bool(wa_opt_in), "note": note})
    except Exception as e:
        app.logger.exception("select buyers failed")
        flash(f"Gagal load data: {e}", "error")
    return render_template("pembeli.html", buyers=buyers, q=q)

@app.post("/pembeli/delete")
@login_required
def pembeli_delete():
    bid = request.form.get("id")
    if not bid:
        flash("ID tidak valid", "error")
        return redirect(url_for("pembeli_page"))
    try:
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM buyers WHERE id=%s", (bid,))
        flash("Pembeli dihapus", "success")
    except Exception as e:
        app.logger.exception("delete buyer failed")
        flash(f"Gagal hapus: {e}", "error")
    return redirect(url_for("pembeli_page"))

@app.route("/pemodal", methods=["GET", "POST"])
@login_required
def pemodal_page():
#    return render_template("base.html", page_title="Pemodal", body="<div class='p-4'>Halaman Pemodal (sementara)</div>")
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        year = int(request.form.get("year") or 0)
        amount = int(request.form.get("amount_idr") or 0)
        note = request.form.get("note") or ""
        if not name or not (2000 <= year <= 2100):
            flash("Nama/Tahun tidak valid", "error")
            return redirect(url_for("pemodal_page", year=year or None))
        try:
            with db_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO investors (name, year, amount_idr, note)
                        VALUES (%s,%s,%s,%s)
                    """, (name, year, amount, note))
            flash("Data pemodal ditambahkan", "success")
        except Exception as e:
            app.logger.exception("insert investor failed")
            flash(f"Gagal simpan: {e}", "error")
        return redirect(url_for("pemodal_page", year=year or None))

    # GET list + summary
    year = request.args.get("year", type=int)
    investors, summary = [], []
    try:
        with db_conn() as conn:
            with conn.cursor() as cur:
                if year:
                    cur.execute("""
                        SELECT id, name, year, amount_idr, note
                        FROM investors
                        WHERE year = %s
                        ORDER BY created_at DESC, name ASC
                    """, (year,))
                else:
                    cur.execute("""
                        SELECT id, name, year, amount_idr, note
                        FROM investors
                        ORDER BY created_at DESC, name ASC
                    """)
                for rid, name, y, amt, note in cur.fetchall():
                    investors.append({"id": rid, "name": name, "year": y, "amount_idr": int(amt), "note": note})

                # summary per tahun
                cur.execute("""
                    SELECT year, COUNT(*) AS cnt, COALESCE(SUM(amount_idr),0)::BIGINT AS total
                    FROM investors
                    GROUP BY year
                    ORDER BY year DESC
                """)
                for y, cnt, total in cur.fetchall():
                    summary.append({"year": y, "count": int(cnt), "total_idr": int(total)})
    except Exception as e:
        app.logger.exception("select investors failed")
        flash(f"Gagal load data: {e}", "error")

    return render_template("pemodal.html", investors=investors, summary=summary, year=year)

@app.post("/pemodal/delete")
@login_required
def pemodal_delete():
    iid = request.form.get("id")
    if not iid:
        flash("ID tidak valid", "error")
        return redirect(url_for("pemodal_page"))
    try:
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM investors WHERE id=%s", (iid,))
        flash("Data pemodal dihapus", "success")
    except Exception as e:
        app.logger.exception("delete investor failed")
        flash(f"Gagal hapus: {e}", "error")
    return redirect(url_for("pemodal_page"))

@app.route("/laporan", methods=["GET"])
@login_required
def laporan_page():
#    return render_template("base.html", page_title="Laporan", body="<div class='p-4'>Halaman Laporan (sementara)</div>")
    # ambil parameter range; default = hari ini
    today = date.today().isoformat()
    from_date = request.args.get("from") or today
    to_date   = request.args.get("to")   or today

    # 1) Bagi hasil via function f_profit_sharing(from,to)
    profit = None
    try:
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM f_profit_sharing(%s,%s)", (from_date, to_date))
                row = cur.fetchone()
                if row:
                    # (range_from, range_to, total_laba, share_karyawan, share_pemodal, share_kas)
                    profit = {
                        "range_from": row[0],
                        "range_to": row[1],
                        "total_laba": int(row[2] or 0),
                        "share_karyawan": int(row[3] or 0),
                        "share_pemodal": int(row[4] or 0),
                        "share_kas": int(row[5] or 0)
                    }
    except Exception as e:
        app.logger.warning("f_profit_sharing error: %s. Fallback aggregate sales.", e)
        # fallback jika function belum ada: hitung dari tabel sales
        try:
            with db_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT COALESCE(SUM(total_profit),0)::BIGINT
                        FROM sales
                        WHERE sale_date BETWEEN %s AND %s
                    """, (from_date, to_date))
                    total = int(cur.fetchone()[0] or 0)
                    profit = {
                        "range_from": from_date,
                        "range_to": to_date,
                        "total_laba": total,
                        "share_karyawan": total*30//100,
                        "share_pemodal":  total*35//100,
                        "share_kas":      total*35//100,
                    }
        except Exception as ee:
            app.logger.exception("fallback profit failed: %s", ee)
            profit = None

    # 2) Rekap harian via view v_sales_by_day
    rekap = []
    try:
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT day, trx_count, total_penjualan, total_modal, total_laba
                    FROM v_sales_by_day
                    WHERE day BETWEEN %s AND %s
                    ORDER BY day
                """, (from_date, to_date))
                for d, c, tp, tm, tl in cur.fetchall():
                    rekap.append({
                        "day": d.isoformat() if hasattr(d, "isoformat") else str(d),
                        "trx_count": int(c or 0),
                        "total_penjualan": int(tp or 0),
                        "total_modal": int(tm or 0),
                        "total_laba": int(tl or 0)
                    })
    except Exception as e:
        app.logger.warning("v_sales_by_day error: %s. Fallback group by sales.", e)
        # fallback jika view belum ada
        try:
            with db_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT sale_date AS day,
                               COUNT(*) AS trx_count,
                               SUM(total_amount)::BIGINT AS total_penjualan,
                               SUM(total_cost)::BIGINT   AS total_modal,
                               SUM(total_profit)::BIGINT AS total_laba
                        FROM sales
                        WHERE sale_date BETWEEN %s AND %s
                        GROUP BY sale_date
                        ORDER BY sale_date
                    """, (from_date, to_date))
                    for d, c, tp, tm, tl in cur.fetchall():
                        rekap.append({
                            "day": d.isoformat() if hasattr(d, "isoformat") else str(d),
                            "trx_count": int(c or 0),
                            "total_penjualan": int(tp or 0),
                            "total_modal": int(tm or 0),
                            "total_laba": int(tl or 0)
                        })
        except Exception as ee:
            app.logger.exception("fallback rekap failed: %s", ee)
            rekap = []

    # 3) Daftar transaksi pada rentang yang sama
    trx = []
    try:
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT s.id,
                           s.sale_date,
                           COALESCE(b.name,'-') AS buyer_name,
                           s.total_amount, s.total_cost, s.total_profit,
                           s.paid_amount, s.change_amount,
                           s.wa_status,
                           s.created_at
                    FROM sales s
                    LEFT JOIN buyers b ON b.id = s.buyer_id
                    WHERE s.sale_date BETWEEN %s AND %s
                    ORDER BY s.sale_date DESC, s.created_at DESC
                    LIMIT 1000
                """, (from_date, to_date))
                for (sid, day, buyer_name, tot, cost, profit, paid, change, wa, created_at) in cur.fetchall():
                    trx.append({
                        "id": str(sid),
                        "sale_date": day.isoformat() if hasattr(day, "isoformat") else str(day),
                        "buyer_name": buyer_name,
                        "total_amount": int(tot or 0),
                        "total_cost": int(cost or 0),
                        "total_profit": int(profit or 0),
                        "paid_amount": int(paid or 0),
                        "change_amount": int(change or 0),
                        "wa_status": wa or "none",
                        "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at)
                    })
    except Exception as e:
        app.logger.exception("load trx failed: %s", e)
        trx = []

    return render_template(
        "laporan.html",
        from_date=from_date,
        to_date=to_date,
        profit=profit,
        rekap=rekap,
        trx=trx
    )

@app.get("/laporan/sale/<sale_id>")
@login_required
def laporan_sale_detail(sale_id):
    """
    Return JSON header + items untuk sebuah transaksi.
    """
    header = None
    items = []
    try:
        with db_conn() as conn:
            with conn.cursor() as cur:
                # Header
                cur.execute("""
                    SELECT s.id, s.sale_date, COALESCE(b.name,'-') AS buyer_name,
                           s.total_amount, s.total_cost, s.total_profit,
                           s.paid_amount, s.change_amount, s.wa_status, s.wa_sent_at
                    FROM sales s
                    LEFT JOIN buyers b ON b.id = s.buyer_id
                    WHERE s.id = %s
                """, (sale_id,))
                row = cur.fetchone()
                if not row:
                    return {"ok": False, "error": "Transaksi tidak ditemukan"}, 404
                header = {
                    "id": str(row[0]),
                    "sale_date": row[1].isoformat() if hasattr(row[1], "isoformat") else str(row[1]),
                    "buyer_name": row[2],
                    "total_amount": int(row[3] or 0),
                    "total_cost": int(row[4] or 0),
                    "total_profit": int(row[5] or 0),
                    "paid_amount": int(row[6] or 0),
                    "change_amount": int(row[7] or 0),
                    "wa_status": row[8] or "none",
                    "wa_sent_at": row[9].isoformat() if row[9] else None
                }
                # Items
                cur.execute("""
                    SELECT item_name, sale_price, qty, line_total
                    FROM sale_items
                    WHERE sale_id = %s
                    ORDER BY created_at
                """, (sale_id,))
                for name, price, qty, subtotal in cur.fetchall():
                    items.append({
                        "item_name": name,
                        "sale_price": int(price or 0),
                        "qty": int(qty or 0),
                        "line_total": int(subtotal or 0)
                    })
    except Exception as e:
        app.logger.exception("detail trx error: %s", e)
        return {"ok": False, "error": str(e)}, 500

    return {"ok": True, "header": header, "items": items}

@app.post("/laporan/sale/<sale_id>/resend-wa")
@login_required
def laporan_resend_wa(sale_id):
    """
    Kirim ulang nota WA untuk transaksi ini.
    Ambil header + items dari DB, bangun teks nota, panggil API WA,
    lalu update wa_status & wa_sent_at.
    """
    # Ambil header + items
    try:
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT s.sale_date, COALESCE(b.name,'') AS buyer_name, COALESCE(b.phone_e164,'') AS phone,
                           s.total_amount, s.paid_amount, s.change_amount
                    FROM sales s
                    LEFT JOIN buyers b ON b.id = s.buyer_id
                    WHERE s.id = %s
                """, (sale_id,))
                row = cur.fetchone()
                if not row:
                    return {"ok": False, "error": "Transaksi tidak ditemukan"}, 404
                sale_date, buyer_name, phone, total_amount, paid_amount, change_amount = row

                cur.execute("""
                    SELECT item_name, sale_price, qty
                    FROM sale_items
                    WHERE sale_id = %s
                    ORDER BY created_at
                """, (sale_id,))
                items = [{"nama": n, "jual": int(p or 0), "qty": int(q or 0)} for (n, p, q) in cur.fetchall()]
    except Exception as e:
        app.logger.exception("resend header/items error: %s", e)
        return {"ok": False, "error": str(e)}, 500

    if not phone:
        return {"ok": False, "error": "Pembeli tidak punya nomor WA"}, 400

    # Build teks nota
    message_text = build_receipt_text(
        sale_date=sale_date.isoformat() if hasattr(sale_date,"isoformat") else str(sale_date),
        buyer_name=buyer_name or "",
        items=items,
        total=int(total_amount or 0),
        paid=int(paid_amount or 0),
        change=int(change_amount or 0)
    )
    number = (phone or "").lstrip("+").strip()

    # Kirim
    try:
        resp = requests.post(
            "https://blast.sukipli.work/send-message",
            json={"number": number, "message": message_text},
            timeout=10
        )
        ok = 200 <= resp.status_code < 300
        with db_conn() as conn:
            with conn.cursor() as cur:
                if ok:
                    cur.execute("UPDATE sales SET wa_status='sent', wa_sent_at=now() WHERE id=%s", (sale_id,))
                else:
                    cur.execute("UPDATE sales SET wa_status='failed' WHERE id=%s", (sale_id,))
        app.logger.info("Resend WA status=%s body=%s", resp.status_code, resp.text)
        if not ok:
            return {"ok": False, "error": f"Gagal kirim WA (HTTP {resp.status_code})"}
    except Exception as e:
        app.logger.warning("Resend WA failed: %s", e)
        try:
            with db_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE sales SET wa_status='failed' WHERE id=%s", (sale_id,))
        except Exception:
            pass
        return {"ok": False, "error": "Exception saat kirim WA"}

    return {"ok": True}

# Contoh: tes koneksi DB (opsional, hapus kalau tidak perlu)
@app.route("/health")
def health():
    ok = True
    msg = "ok"
    if db_pool:
        try:
            with db_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    cur.fetchone()
        except Exception as e:
            ok = False
            msg = f"db error: {e}"
    return {"ok": ok, "msg": msg}

# =========================
# Run
# =========================
if __name__ == "__main__":
    # Gunakan host 0.0.0.0 agar bisa diakses dari jaringan (jika di docker)
    app.run(host="0.0.0.0", port=5000, debug=os.getenv("FLASK_DEBUG") == "1")
