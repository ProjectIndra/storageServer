from flask import Flask, request, send_file, jsonify
import os
import subprocess
import tempfile
import shutil

app = Flask(__name__)
HDFS_BASE_DIR = ""  # Change this to your HDFS base directory

def hdfs_exists(path):
    cmd = ["hdfs", "dfs", "-test", "-e", path]
    return subprocess.run(cmd).returncode == 0

def is_hdfs_dir(path):
    cmd = ["hdfs", "dfs", "-test", "-d", path]
    return subprocess.run(cmd).returncode == 0

@app.route("/removeSafeMode", methods=["GET"])
def remove_safe_mode():
    """
    This endpoint is used to remove the safe mode from HDFS
    """
    cmd = ["hdfs", "dfsadmin", "-safemode", "leave"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode != 0:
        return jsonify({"error": "Failed to leave safe mode"}), 500
    
    return jsonify({"message": "Safe mode removed successfully"}), 200

@app.route("/upload", methods=["POST"])
def upload_file():
    file = request.files.get("file")
    path = request.form.get("path")

    if not file or not path:
        return jsonify({"error": "Missing file or path parameter"}), 400

    # Sanitize file name and HDFS path
    filename = os.path.basename(file.filename)  # Removes directory traversal attempts
    hdfs_path = f"{HDFS_BASE_DIR}/{path}/{filename}"

    # Use a temporary file to safely handle spaces and special characters
    with tempfile.NamedTemporaryFile(delete=False) as temp_file:
        file.save(temp_file.name)
        local_path = temp_file.name

    try:
        # Upload to HDFS
        cmd = ["hdfs", "dfs", "-put", "-f", local_path, hdfs_path]
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            print(f"Error uploading file to HDFS:\n{result.stderr}")
            return jsonify({"error": "Failed to upload to HDFS"}), 500

    finally:
        # Always remove the temp file
        if os.path.exists(local_path):
            os.remove(local_path)

    if not hdfs_exists(hdfs_path):
        print(f"File not found in HDFS: {hdfs_path}")
        return jsonify({"error": "File upload failed"}), 500

    return jsonify({
        "message": "File uploaded to HDFS successfully",
        "path": hdfs_path
    }), 200


@app.route("/uploadFolder", methods=["POST"])
def upload_folder():
    """
    User will give a zip file that contains a folder
    so , this server file will unzip the folder and upload it to HDFS
    """
    file = request.files.get("file")
    path = request.form.get("path")

    if not file or not path:
        return jsonify({"error": "Missing file or path parameter"}), 400
    
    local_path = f"/tmp/{file.filename}"
    file.save(local_path)

    # Unzip the file
    unzip_dir = f"/tmp/unzipped_{file.filename}"
    os.makedirs(unzip_dir, exist_ok=True)
    result = subprocess.run(["unzip", local_path, "-d", unzip_dir], check=True)

    if result.returncode != 0:
        return jsonify({"error": "Unzipping failed"}), 500

    # Upload the the contents of the unzipped folder to HDFS
    hdfs_path = f"{HDFS_BASE_DIR}/{path}"
    cmd = ["hdfs", "dfs", "-put", "-f", f"{unzip_dir}/*", hdfs_path]

    if result.returncode != 0:
        return jsonify({"error": "Folder upload failed"}), 500

    # Clean up local files
    os.remove(local_path)
    subprocess.run(["rm", "-rf", unzip_dir])

    if not hdfs_exists(hdfs_path):
        return jsonify({"error": "Folder upload failed"}), 500
    return jsonify({"message": "Folder uploaded to HDFS successfully", "path": hdfs_path}), 200

@app.route("/download", methods=["POST"])
def download_file():
    data = request.get_json()
    path = data.get("path")
    if not path:
        return jsonify({"error": "Missing 'path' parameter"}), 400
    
    hdfs_path = f"{HDFS_BASE_DIR}/{path}"
    
    # Use a temporary file to safely handle spaces and special characters
    with tempfile.NamedTemporaryFile(delete=False) as temp_file:
        local_path = temp_file.name

    try:
        # Download from HDFS
        cmd = ["hdfs", "dfs", "-get", "-f", hdfs_path, local_path]
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            print(f"Error downloading file from HDFS:\n{result.stderr}")
            return jsonify({"error": "Failed to download from HDFS"}), 500

        if not os.path.exists(local_path):
            print(f"File not found locally after download: {local_path}")
            return jsonify({"error": "File download failed"}), 500

        return send_file(local_path, as_attachment=True)

    finally:
        # Always remove the temp file after sending
        if os.path.exists(local_path):
            os.remove(local_path)

@app.route("/mkdir", methods=["POST"])
def create_directory():
    data = request.get_json()
    path = data.get("path")
    if not path:
        return jsonify({"error": "Missing 'path' parameter"}), 400
    
    hdfs_path = f"{HDFS_BASE_DIR}/{path}"
    cmd = ["hdfs", "dfs", "-mkdir", "-p", hdfs_path]
    subprocess.run(cmd, check=True)
    
    if not is_hdfs_dir(hdfs_path):
        return jsonify({"error": "Directory creation failed"}), 500
    
    return jsonify({"message": "Directory created in HDFS", "path": hdfs_path})

@app.route("/list", methods=["POST"])
def list_contents():
    data = request.get_json()
    path = data.get("path", "")
    hdfs_path = f"{HDFS_BASE_DIR}/{path}".rstrip("/")

    print(f"Listing contents of: {hdfs_path}")
    if not hdfs_exists(hdfs_path):
        return jsonify({"error": "Path does not exist"}), 400

    cmd = ["hdfs", "dfs", "-ls", hdfs_path]
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        return jsonify({"error": result.stderr.strip()}), 500

    lines = result.stdout.strip().split("\n")[1:]  # skip first line (like "Found X items")

    entries = []
    for line in lines:
        parts = line.split(maxsplit=7)
        if len(parts) < 8:
            continue  # skip malformed lines

        permission, _, owner, group, size, date, time, full_path = parts
        entry_type = "DIRECTORY" if permission.startswith("d") else "FILE"
        name = full_path.split("/")[-1]

        entries.append({
            "name": name,
            "path": full_path,
            "type": entry_type,
            "permission": permission,
            "owner": owner,
            "group": group,
            "size": size,
            "lastModified": f"{date} {time}",
            "replication": "-",      # add actual value if needed
            "blockSize": "-",        # add actual value if needed
            "fileDescription": "N/A" # replace if you store descriptions elsewhere
        })

    print(f"Entries found: {entries}")
    return jsonify({ "contents": entries })

@app.route("/delete", methods=["POST"])
def delete_path():
    data = request.get_json()
    path = data.get("path")
    if not path:
        return jsonify({"error": "Missing 'path' parameter"}), 400
    
    hdfs_path = f"{HDFS_BASE_DIR}/{path}"
    print(f"Deleting path: {hdfs_path}")
    
    if not hdfs_exists(hdfs_path):
        return jsonify({"error": "Path does not exist"}), 400
    
    cmd = ["hdfs", "dfs", "-rm", "-r", hdfs_path]
    subprocess.run(cmd, check=True)
    
    if hdfs_exists(hdfs_path):
        return jsonify({"error": "Deletion failed"}), 500
    
    return jsonify({"message": "Deleted from HDFS", "path": hdfs_path})

@app.route("/rename", methods=["POST"])
def rename_path():
    data = request.get_json()
    old_path = data.get("old_path")
    new_path = data.get("new_path")
    
    if not old_path or not new_path:
        return jsonify({"error": "Missing 'old_path' or 'new_path' parameter"}), 400
    
    old_hdfs_path = f"{HDFS_BASE_DIR}/{old_path}"
    new_hdfs_path = f"{HDFS_BASE_DIR}/{new_path}"
    if not hdfs_exists(old_hdfs_path):
        return jsonify({"error": "Source path does not exist"}), 400
    
    cmd = ["hdfs", "dfs", "-mv", old_hdfs_path, new_hdfs_path]
    subprocess.run(cmd, check=True)
    
    
    if not hdfs_exists(new_hdfs_path):
        return jsonify({"error": "Rename failed"}), 500
    
    return jsonify({"message": "Renamed in HDFS", "old_path": old_path, "new_path": new_path})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
