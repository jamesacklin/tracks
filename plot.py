import os
import random
import gpxpy
import matplotlib.pyplot as plt
import datetime
import logging
from getpass import getpass
import requests
from garth.exc import GarthHTTPError
from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)
import time
from datetime import datetime, timedelta
from flask import Flask, send_from_directory
from threading import Thread

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables if defined
email = os.getenv("EMAIL")
password = os.getenv("PASSWORD")
tokenstore = os.getenv("GARMINTOKENS") or "~/.garminconnect"
api = None

app = Flask(__name__)

# Define output_dir globally
output_dir = None

def get_credentials():
    """Get user credentials."""
    email = input("Login e-mail: ")
    password = getpass("Enter password: ")
    return email, password

def init_api(email, password):
    """Initialize Garmin API with your credentials."""
    try:
        print(f"Trying to login to Garmin Connect using token data from directory '{tokenstore}'...\n")
        garmin = Garmin()
        garmin.login(tokenstore)
    except (FileNotFoundError, GarthHTTPError, GarminConnectAuthenticationError):
        print("Login tokens not present or invalid, login with your Garmin Connect credentials to generate them.\n"
              f"They will be stored in '{tokenstore}' for future use.\n")
        try:
            if not email or not password:
                email, password = get_credentials()
            garmin = Garmin(email=email, password=password, is_cn=False, prompt_mfa=get_mfa)
            garmin.login()
            garmin.garth.dump(tokenstore)
            print(f"Oauth tokens stored in '{tokenstore}' directory for future use.\n")
        except (FileNotFoundError, GarthHTTPError, GarminConnectAuthenticationError, requests.exceptions.HTTPError) as err:
            logger.error(err)
            return None
    return garmin

def get_mfa():
    """Get MFA."""
    return input("MFA one-time code: ")

def plot_gpx(gpx_data, output_file='track.png', start_time_text=''):
    """Plot GPX data."""
    gpx = gpxpy.parse(gpx_data)
    latitudes = []
    longitudes = []
    jitter_amount = 0.00001
    for track in gpx.tracks:
        for segment in track.segments:
            for point in segment.points:
                latitudes.append(point.latitude + random.uniform(-jitter_amount, jitter_amount))
                longitudes.append(point.longitude + random.uniform(-jitter_amount, jitter_amount))
    plt.figure(figsize=(6, 6))
    plt.plot(longitudes, latitudes, 'k-', linewidth=2, solid_capstyle='round')
    plt.axis('equal')
    plt.axis('off')
    
    # Overlay the start time text in the upper-left corner
    # plt.text(0.01, 0.99, start_time_text, transform=plt.gca().transAxes,
    #          fontsize=8, verticalalignment='top', horizontalalignment='left',
    #          fontdict={'family': 'monospace', 'weight': 'light'},
    #          bbox=dict(facecolor='white', alpha=0.5, edgecolor='none'))
    
    plt.savefig(output_file, bbox_inches='tight', pad_inches=0, dpi=300)
    plt.close()
    print(f"Track plotted and saved as {output_file}")

def load_downloaded_activities(file_path='downloaded_activities.txt'):
    """Load downloaded activity IDs from a file."""
    if not os.path.exists(file_path):
        return set()
    with open(file_path, 'r') as file:
        return set(line.strip() for line in file)

def save_downloaded_activity(activity_id, file_path='downloaded_activities.txt'):
    """Save a downloaded activity ID to a file."""
    with open(file_path, 'a') as file:
        file.write(f"{activity_id}\n")

def download_and_plot_new_activities(api, start_date, end_date, output_dir):
    """Download and plot new activities for the given date range."""
    downloaded_activities = load_downloaded_activities()
    activities = api.get_activities_by_date(start_date, end_date)
    for activity in activities:
        activity_id = activity['activityId']
        if activity_id in downloaded_activities:
            continue
        activity_name = activity["activityName"]
        start_time = datetime.strptime(activity["startTimeLocal"], "%Y-%m-%d %H:%M:%S")
        output_filename = start_time.strftime("%Y-%m-%d-%H-%M") + ".png"
        start_time_text = start_time.strftime("%Y-%m-%d @ %H:%M")
        print(f"Downloading new activity: {activity_name} (ID: {activity_id})")
        gpx_data = api.download_activity(activity_id, dl_fmt=api.ActivityDownloadFormat.GPX)
        output_file = os.path.join(output_dir, output_filename)
        plot_gpx(gpx_data, output_file=output_file, start_time_text=start_time_text)
        save_downloaded_activity(activity_id)

def generate_html(output_dir):
    """Generate an HTML file with all images in the output directory."""
    images = [f for f in os.listdir(output_dir) if f.endswith('.png')]
    images.sort(reverse=True)
    html_content = "<html><head><style type='text/css'>body { text-align: center; margin: 0 auto; padding: 6em; max-width: 600px; } img { width: 100%; height: auto; margin-bottom: 6em; }</style></head><body>\n"
    for image in images:
        html_content += f'<img src="{image}">\n'
    html_content += "</body></html>"

    with open(os.path.join(output_dir, 'index.html'), 'w') as f:
        f.write(html_content)

@app.route('/')
def serve_html():
    """Serve the generated HTML page."""
    return send_from_directory(output_dir, 'index.html')

@app.route('/<path:filename>')
def serve_file(filename):
    """Serve a file from the output directory."""
    return send_from_directory(output_dir, filename)

def main():
    global email, password, output_dir  # Use the global variables
    # Initialize API
    api = init_api(email, password)
    if not api:
        print("Failed to initialize Garmin API.")
        return

    # Determine the script's directory and set the output directory
    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_dir = os.path.join(script_dir, 'output')

    # Create output directory if it doesn't exist
    os.makedirs(output_dir, exist_ok=True)

    # Load downloaded activities once at startup
    downloaded_activities = load_downloaded_activities()

    # Download activities from the past N days
    for i in range(30, 0, -1):
        date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        download_and_plot_new_activities(api, date, date, output_dir)

    # Generate HTML page
    generate_html(output_dir)

    # Run continuously, polling every 20 minutes
    while True:
        # Reload downloaded activities to ensure the latest state
        downloaded_activities = load_downloaded_activities()
        
        current_date = datetime.now().strftime("%Y-%m-%d")
        download_and_plot_new_activities(api, current_date, current_date, output_dir)
        generate_html(output_dir)
        print("Listening for new activities... Next check in 20 minutes.")
        time.sleep(1200)  # Sleep for 20 minutes

if __name__ == "__main__":
    # Determine the script's directory and set the output directory before starting Flask
    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_dir = os.path.join(script_dir, 'output')

    # Start the Flask app in a separate thread
    flask_thread = Thread(target=lambda: app.run(host='0.0.0.0', port=42069))
    flask_thread.start()

    # Run the main function
    main()