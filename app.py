import os
import uuid
import hashlib
import hmac
from urllib.parse import quote

import requests
from flask import Flask, request, jsonify, render_template_string
from werkzeug.utils import secure_filename

app = Flask(__name__)

# Configuration: values will be added privately in Render
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
BUCKET_NAME = os.environ.get("SUPABASE_BUCKET", "Cutomer Upload")

MAX_FILE_SIZE = 15 * 1024 * 1024  # 15 MB
app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_SIZE

ALLOWED_EXTENSIONS = {"pdf", "jpg", "jpeg", "png"}

CUSTOMER_PAGE = """
<!doctype html>
<html lang="en">
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Raza Auto Print - File Upload</title>
<style>
body{font-family:Arial,sans-serif;background:#f2f4f8;margin:0;padding:20px}
.card{max-width:440px;margin:35px auto;background:white;padding:25px;
border-radius:14px;box-shadow:0 3px 18px #0001}
h2{text-align:center;color:#202b45}
input{width:100%;box-sizing:border-box;margin:10px 0;padding:13px;
border:1px solid #ccc;border-radius:8px}
button{width:100%;padding:14px;background:#1769e0;color:white;
border:0;border-radius:8px;font-size:16px;cursor:pointer}
.note{color:#666;font-size:13px;line-height:1.5}
#message{margin-top:15px;line-height:1.5}
</style>
</head>
<body>
<div class="card">
<h2>RAZA AUTO PRINT</h2>
<p style="text-align:center">Upload your document for printing</p>
<form id="uploadForm">
<input name="customer_name" placeholder="Your name" required maxlength="80">
<input name="mobile" placeholder="Mobile number" maxlength="20">
<input type="file" name="file" accept=".pdf,.jpg,.jpeg,.png" required>
<p class="note">Allowed files: PDF, JPG, JPEG, PNG. Maximum size: 15 MB.</p>
<button id="submitBtn" type="submit">Submit File</button>
</form>
<div id="message"></div>
</div>
<script>
const form = document.getElementById("uploadForm");
form.addEventListener("submit", async function(e) {
    e.preventDefault();
    const btn = document.getElementById("submitBtn");
    const msg = document.getElementById("message");
    btn.disabled = true;
    btn.textContent = "Uploading...";
    msg.textContent = "";
    try {
        const response = await fetch(window.location.pathname, {
            method: "POST",
            body: new FormData(form)
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || "Upload failed");
        msg.textContent = "File submitted successfully! Please contact the shop for updates.";
        msg.style.color = "green";
        form.reset();
    } catch (err) {
        msg.textContent = err.message;
        msg.style.color = "red";
    } finally {
        btn.disabled = false;
        btn.textContent = "Submit File";
    }
});
</script>
</body>
</html>
"""


def configured():
    return bool(SUPABASE_URL and SUPABASE_KEY)


def supabase_headers(extra=None):
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
    }
    if extra:
        headers.update(extra)
    return headers


def get_shop(shop_code):
    response = requests.get(
        f"{SUPABASE_URL}/rest/v1/shops",
        headers=supabase_headers(),
        params={
            "shop_code": f"eq.{shop_code}",
            "select": "id,shop_name,shop_code,api_token_hash",
            "limit": "1",
        },
        timeout=20,
    )
    response.raise_for_status()
    shops = response.json()
    return shops[0] if shops else None


def verify_shop_token(shop):
    supplied = request.headers.get("X-Shop-Token", "")
    if not supplied:
        return False
    supplied_hash = hashlib.sha256(supplied.encode("utf-8")).hexdigest()
    stored_hash = shop.get("api_token_hash", "")
    return bool(stored_hash) and hmac.compare_digest(supplied_hash, stored_hash)


def allowed_file(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
    )


def get_print_request(shop_id, request_id):
    response = requests.get(
        f"{SUPABASE_URL}/rest/v1/print_requests",
        headers=supabase_headers(),
        params={
            "id": f"eq.{request_id}",
            "shop_id": f"eq.{shop_id}",
            "select": "*",
            "limit": "1",
        },
        timeout=20,
    )
    response.raise_for_status()
    rows = response.json()
    return rows[0] if rows else None


@app.get("/")
def home():
    return (
        "Raza Auto Print cloud server is running.",
        200,
        {"Content-Type": "text/plain; charset=utf-8"},
    )


@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "Raza Auto Print"})


@app.route("/s/<shop_code>", methods=["GET", "POST"])
def customer_upload(shop_code):
    if not configured():
        return jsonify({"error": "Server configuration is incomplete."}), 503

    try:
        shop = get_shop(shop_code)
    except requests.RequestException:
        app.logger.exception("Could not look up shop")
        return jsonify({"error": "Shop service is temporarily unavailable."}), 503

    if not shop:
        return "Shop not found. Please check your QR code.", 404

    if request.method == "GET":
        return render_template_string(CUSTOMER_PAGE)

    customer_name = request.form.get("customer_name", "").strip()
    mobile = request.form.get("mobile", "").strip()
    uploaded_file = request.files.get("file")

    if not customer_name:
        return jsonify({"error": "Please enter your name."}), 400

    if not uploaded_file or not uploaded_file.filename:
        return jsonify({"error": "Please select a file."}), 400

    original_name = secure_filename(uploaded_file.filename)
    if not original_name or not allowed_file(original_name):
        return jsonify({"error": "Only PDF, JPG, JPEG and PNG files are allowed."}), 400

    file_bytes = uploaded_file.read(MAX_FILE_SIZE + 1)
    if not file_bytes:
        return jsonify({"error": "The selected file is empty."}), 400
    if len(file_bytes) > MAX_FILE_SIZE:
        return jsonify({"error": "File is larger than 15 MB."}), 413

    extension = original_name.rsplit(".", 1)[1].lower()
    storage_path = f"{shop['id']}/{uuid.uuid4().hex}.{extension}"

    storage_url = (
        f"{SUPABASE_URL}/storage/v1/object/"
        f"{quote(BUCKET_NAME, safe='')}/{quote(storage_path, safe='/')}"
    )

    try:
        upload_response = requests.post(
            storage_url,
            headers=supabase_headers({
                "Content-Type": uploaded_file.mimetype or "application/octet-stream",
                "x-upsert": "false",
            }),
            data=file_bytes,
            timeout=60,
        )
        upload_response.raise_for_status()

        insert_response = requests.post(
            f"{SUPABASE_URL}/rest/v1/print_requests",
            headers=supabase_headers({
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            }),
            json={
                "shop_id": shop["id"],
                "customer_name": customer_name,
                "mobile": mobile,
                "original_file_name": original_name,
                "storage_path": storage_path,
                "status": "pending",
            },
            timeout=30,
        )
        insert_response.raise_for_status()

        return jsonify({
            "success": True,
            "message": "Your file has been submitted.",
        }), 201

    except requests.RequestException:
        app.logger.exception("Upload or database insert failed")
        return jsonify({
            "error": "Upload could not be completed. Please try again."
        }), 503


@app.get("/api/<shop_code>/requests")
def list_requests(shop_code):
    if not configured():
        return jsonify({"error": "Server configuration is incomplete."}), 503

    try:
        shop = get_shop(shop_code)
        if not shop:
            return jsonify({"error": "Shop not found."}), 404
        if not verify_shop_token(shop):
            return jsonify({"error": "Unauthorized."}), 401

        response = requests.get(
            f"{SUPABASE_URL}/rest/v1/print_requests",
            headers=supabase_headers(),
            params={
                "shop_id": f"eq.{shop['id']}",
                "select": "id,customer_name,mobile,original_file_name,status,created_at",
                "status": "eq.pending",
                "order": "created_at.asc",
            },
            timeout=30,
        )
        response.raise_for_status()
        return jsonify(response.json())

    except requests.RequestException:
        app.logger.exception("Could not list requests")
        return jsonify({"error": "Could not retrieve requests."}), 503


@app.get("/api/<shop_code>/requests/<request_id>/file")
def download_request_file(shop_code, request_id):
    if not configured():
        return jsonify({"error": "Server configuration is incomplete."}), 503

    try:
        shop = get_shop(shop_code)
        if not shop:
            return jsonify({"error": "Shop not found."}), 404
        if not verify_shop_token(shop):
            return jsonify({"error": "Unauthorized."}), 401

        print_request = get_print_request(shop["id"], request_id)
        if not print_request:
            return jsonify({"error": "Request not found."}), 404

        storage_url = (
            f"{SUPABASE_URL}/storage/v1/object/"
            f"{quote(BUCKET_NAME, safe='')}/"
            f"{quote(print_request['storage_path'], safe='/')}"
        )
        response = requests.get(
            storage_url,
            headers=supabase_headers(),
            timeout=60,
        )
        response.raise_for_status()

        from flask import Response
        return Response(
            response.content,
            content_type=response.headers.get(
                "Content-Type", "application/octet-stream"
            ),
            headers={
                "Content-Disposition": (
                    "attachment; filename="
                    f'"{secure_filename(print_request["original_file_name"])}"'
                )
            },
        )

    except requests.RequestException:
        app.logger.exception("Could not download request file")
        return jsonify({"error": "Could not download the file."}), 503


@app.patch("/api/<shop_code>/requests/<request_id>/status")
def update_request_status(shop_code, request_id):
    if not configured():
        return jsonify({"error": "Server configuration is incomplete."}), 503

    try:
        shop = get_shop(shop_code)
        if not shop:
            return jsonify({"error": "Shop not found."}), 404
        if not verify_shop_token(shop):
            return jsonify({"error": "Unauthorized."}), 401

        data = request.get_json(silent=True) or {}
        new_status = data.get("status")
        if new_status not in {"approved", "printed", "rejected"}:
            return jsonify({"error": "Invalid status."}), 400

        print_request = get_print_request(shop["id"], request_id)
        if not print_request:
            return jsonify({"error": "Request not found."}), 404

        response = requests.patch(
            f"{SUPABASE_URL}/rest/v1/print_requests",
            headers=supabase_headers({
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            }),
            params={
                "id": f"eq.{request_id}",
                "shop_id": f"eq.{shop['id']}",
            },
            json={"status": new_status},
            timeout=30,
        )
        response.raise_for_status()
        return jsonify({"success": True, "status": new_status})

    except requests.RequestException:
        app.logger.exception("Could not update request status")
        return jsonify({"error": "Could not update request status."}), 503


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
