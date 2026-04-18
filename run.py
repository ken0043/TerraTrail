"""Entry-point: `python run.py` starts the TerraTrail web UI."""
from terratrail.app import create_app

if __name__ == "__main__":
    app = create_app()
    print("TerraTrail server starting on http://127.0.0.1:5000/")
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
