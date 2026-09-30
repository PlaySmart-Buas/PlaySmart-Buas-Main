"""Gaze overlay and OpenFace emotion recorder to data/emotion/<session_id>_emotion.csv.

Modified 2026-09 (iteration 5): file named from the shared session id; stops on the
orchestrator's stop signal; the emotion readout is drawn on-screen at 1080p (it was at
a hardcoded y=1400); only the tail of OpenFace's growing CSV is read each tick; a missing
OpenFace binary (or PLAYSMART_OPENFACE=0) leaves this running as a gaze-only overlay
instead of taking the gaze dot down with it.
"""

import sys
import os
import pygame
import tobii_research as tr
import screeninfo
import keyboard
import subprocess
import pandas as pd
import glob
import time
from datetime import datetime
from pathlib import Path
import threading

sys.path.insert(0, str(Path(__file__).resolve().parent))
import session

# Paths and constants
openface_executable = 'OpenFace_2.2.0_win_x64/FeatureExtraction.exe'
output_dir = 'output'
os.makedirs(output_dir, exist_ok=True)
circle_radius = 50
outline_thickness = 3

# OpenFace is the most expensive thing in the capture stack and the emotion
# labels it feeds come out ~95% Neutral on real gameplay. Set
# PLAYSMART_OPENFACE=0 to run this as a gaze overlay only — no webcam, no
# FeatureExtraction.exe, no emotion CSV. Default stays on so nothing changes
# until that decision is actually made.
ENABLE_OPENFACE = os.environ.get("PLAYSMART_OPENFACE", "1") == "1"


start_unix_time = int(time.time()*1000)
print (f"Script started at:{start_unix_time} (Unix time: {start_unix_time})")

def convert_unix_to_datetime(unix_time):
    return datetime.fromtimestamp(unix_time)

# Initialize Pygame for visualization
pygame.init()
clock = pygame.time.Clock()
screen = screeninfo.get_monitors()[0]
screen_width, screen_height = screen.width, screen.height
window = pygame.display.set_mode((screen_width, screen_height), pygame.NOFRAME)
pygame.display.set_caption("Gaze and Emotion Overlay")

# Eye Tracker setup
eyetrackers = tr.find_all_eyetrackers()
if not eyetrackers:
    print("No eye trackers found. Running without gaze tracking.")
    my_eyetracker = None
else:
    my_eyetracker = eyetrackers[0]

# Define smoothing parameters
alpha = 0.1
smoothed_x, smoothed_y = 0, 0

def gaze_data_callback(gaze_data):
    """
    Processes gaze data from the eye tracker.
    Author: Thomas Pichardo

    """
    global smoothed_x, smoothed_y
    left_x, left_y = gaze_data['left_gaze_point_on_display_area']
    right_x, right_y = gaze_data['right_gaze_point_on_display_area']
    avg_x = (left_x + right_x) / 2
    avg_y = (left_y + right_y) / 2
    x_screen = int(avg_x * screen_width)
    y_screen = int(avg_y * screen_height)
    smoothed_x = alpha * x_screen + (1 - alpha) * smoothed_x
    smoothed_y = alpha * y_screen + (1 - alpha) * smoothed_y

# Start OpenFace FeatureExtraction
process = None

if ENABLE_OPENFACE:
    command = [
        openface_executable,
        '-device', '0',
        '-out_dir', output_dir,
        '-aus'
    ]
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except OSError as exc:
        # OpenFace is not installed on every lab machine. An uncaught
        # FileNotFoundError here used to take the whole overlay down with it,
        # which also cost the on-screen gaze dot - the operator's only live
        # confirmation that the tracker is working at all. Emotion is optional;
        # the dot is not. Carry on without it.
        print("!" * 68)
        print(f"!!  OpenFace could not start: {exc}")
        print(f"!!  looked for: {openface_executable}")
        print("!!  Continuing as a gaze overlay only - no emotion capture this session.")
        print("!!  Set PLAYSMART_OPENFACE=0 to make this the intended behaviour.")
        print("!" * 68, flush=True)
        ENABLE_OPENFACE = False
        process = None

if ENABLE_OPENFACE:
    def monitor_openface():
        global start_unix_time
        for line in process.stdout:
            if "Starting tracking" in line:
                start_unix_time = int(time.time()*1000)
                print(f"Tracking started at: {start_unix_time}")
                break

    monitor_thread = threading.Thread(target=monitor_openface)
    monitor_thread.start()
elif os.environ.get("PLAYSMART_OPENFACE", "1") == "0":
    print("PLAYSMART_OPENFACE=0 — gaze overlay only, no webcam and no emotion capture.")

# Load calibration if available
if my_eyetracker:
    calibration_data = my_eyetracker.retrieve_calibration_data()
    if calibration_data:
        my_eyetracker.apply_calibration_data(calibration_data)
        print("Calibration loaded.")
    my_eyetracker.subscribe_to(tr.EYETRACKER_GAZE_DATA, gaze_data_callback, as_dictionary=True)

# Define emotion mapping function
def map_emotion(row):
    """
    Maps facial action units to emotions.
    Action units are taken to calculate emotions based on the position of the action units

    Author: Thomas Pichardo

    """
    try:
        if row.get('AU06_c', 0) == 1 and row.get('AU12_c', 0) == 1 and row.get('AU25_c', 0) == 1:
            return 'Happiness'
        elif row.get('AU01_c', 0) == 1 and row.get('AU04_c', 0) == 1 and row.get('AU15_c', 0) == 1 and row.get('AU17_c', 0) == 1:
            return 'Sadness'
        elif row.get('AU05_c', 0) == 1 and row.get('AU26_c', 0) == 1 and row.get('AU02_c', 0) == 1 and row.get('AU07_c', 0) == 1:
            return 'Surprise'
        elif row.get('AU09_c', 0) == 1 and row.get('AU10_c', 0) == 1 and row.get('AU14_c', 0) == 1:
            return 'Disgust'
        elif row.get('AU01_c', 0) == 1 and row.get('AU02_c', 0) == 1 and row.get('AU04_c', 0) == 1 and row.get('AU05_c', 0) == 1 and row.get('AU07_c', 0) == 1:
            return 'Fear'
        elif row.get('AU04_c', 0) == 1 and row.get('AU07_c', 0) == 1 and row.get('AU23_c', 0) == 1 and row.get('AU25_c', 0) == 1:
            return 'Anger'
        else:
            return 'Neutral'
    except KeyError as e:
        print(f"Missing column in DataFrame: {e}")
        return 'Unknown'

def save_emotion_data():
    """
    Saves processed emotion data to a CSV file.
    Author: Thomas Pichardo

    """
    if not ENABLE_OPENFACE:
        return

    csv_files = glob.glob(os.path.join(output_dir, '*.csv'))
    if not csv_files:
        print("No CSV files found.")
        return

    latest_csv = max(csv_files, key=os.path.getctime)
    df = pd.read_csv(latest_csv)
    df.columns = df.columns.str.strip()

    df['emotion'] = df.apply(map_emotion, axis=1)

    if 'timestamp' in df.columns:
        df['unix_time'] = df['timestamp'].apply(lambda x: int(start_unix_time + x * 1000))
        df['datetime'] = df['unix_time'].apply(lambda  x: datetime.fromtimestamp(x / 1000.0))
    else:
        print("Warning: 'timestamp' column not found in emotion data. UNIX time not assigned.")
        df['unix_time'] = None
        df['datetime'] = None

    df = df[['datetime', 'unix_time', 'emotion', 'confidence']]
    save_file_path = str(session.stream_path("emotion"))
    df.to_csv(save_file_path, index=False)
    print(f"Saved emotion data with timestamps to {save_file_path}")

_stopped = threading.Event()


def stop_recording():
    """
    Stops recording and saves emotion data.
    Author: Thomas Pichardo

    """
    if _stopped.is_set():
        return
    _stopped.set()
    print('Stopping webcam recording...')
    save_emotion_data()
    if process is not None:
        process.terminate()
    print('Webcam recording stopped.')


def listen_for_stop():
    """Watch the shared stop signal (stop file, or F12 for a manual abort).

    Automatic start/stop means there is no F12 press to hook, so this polls
    instead of registering a hotkey.
    """
    while not _stopped.is_set():
        if session.stop_requested():
            stop_recording()
            return
        time.sleep(0.25)


hotkey_thread = threading.Thread(target=listen_for_stop, daemon=True)
hotkey_thread.start()

# Define colors for each emotion
emotion_colors = {
    'Happiness': (255, 255, 0),  # Yellow
    'Sadness': (0, 0, 255),      # Blue
    'Surprise': (255, 165, 0),   # Orange
    'Disgust': (128, 0, 128),    # Purple
    'Fear': (255, 192, 203),    # Pink
    'Anger': (255, 0, 0),         # Red
    'Neutral': (255, 255, 255)   # White
}

# Emotion update interval
emotion_update_interval = 0.5
last_emotion_check_time = time.time()

# Where the live emotion readout is drawn. This used to be hardcoded at y=1400,
# which is off-screen on a 1080p display — which is why the recordings show the
# gaze circle and no emotion box. Anchor it to the bottom of the actual screen.
readout_rect = (10, screen_height - 60, 280, 32)


def latest_emotion(path, _cache={}):
    """Read only the last row of the OpenFace CSV.

    The old version re-read the entire growing file and re-applied map_emotion
    to every row, twice a second, for the whole session — by minute 45 that is
    ~11k rows reparsed on every tick. Seek to the end and parse one line
    against the cached header instead.
    """
    header = _cache.get(path)
    try:
        with open(path, 'rb') as fh:
            if header is None:
                header = fh.readline().decode('utf-8', 'replace').strip().split(',')
                header = [h.strip() for h in header]
                _cache[path] = header
            fh.seek(0, os.SEEK_END)
            end = fh.tell()
            block = min(8192, end)
            fh.seek(end - block)
            lines = [ln for ln in fh.read(block).splitlines() if ln.strip()]
        if len(lines) < 2:
            return None
        values = lines[-1].decode('utf-8', 'replace').split(',')
        if len(values) != len(header):
            return None
        row = {}
        for k, v in zip(header, values):
            v = v.strip()
            try:
                row[k] = float(v)
            except ValueError:
                row[k] = v
        return map_emotion(row)
    except (OSError, ValueError):
        return None


# Visualization loop
try:
    emotion = 'Neutral'
    while not _stopped.is_set():
        pygame.event.pump()

        # Process events (important to close the window correctly)
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                stop_recording()  
                pygame.quit()  
                sys.exit()  

        # Clear screen
        window.fill((0, 0, 0))

        # Draw gaze overlay
        pygame.draw.circle(window, (255, 255, 255), (int(smoothed_x), int(smoothed_y)), circle_radius, outline_thickness)

        # Draw colored rectangle around the detected emotion
        if ENABLE_OPENFACE:
            rect_color = emotion_colors.get(emotion, (255, 255, 255))
            pygame.draw.rect(window, rect_color, readout_rect, 0)
            font = pygame.font.SysFont('Arial', 30)
            text_surface = font.render(f'Emotion: {emotion}', True, (0, 0, 0))
            window.blit(text_surface, (readout_rect[0] + 10, readout_rect[1]))

        # Update display
        pygame.display.flip()

        # Control frame rate
        clock.tick(60)

        # Check for new emotion data periodically
        if ENABLE_OPENFACE and time.time() - last_emotion_check_time > emotion_update_interval:
            csv_files = glob.glob(os.path.join(output_dir, '*.csv'))
            if csv_files:
                latest_csv = max(csv_files, key=os.path.getctime)
                found = latest_emotion(latest_csv)
                if found is not None:
                    emotion = found
            last_emotion_check_time = time.time()

        if keyboard.is_pressed("f12"):
            break
finally:
    process.terminate()
    process.wait()
    my_eyetracker.unsubscribe_from(tr.EYETRACKER_GAZE_DATA, gaze_data_callback)
    pygame.quit()

    dir_path = 'output'
    for filename in os.listdir(dir_path):
        file_path = os.path.join(dir_path, filename)
        for attempt in range(10):
            try:
                os.remove(file_path)
                print(f"Deleted file: {filename}")
                break
            except PermissionError:
                time.sleep(0.5)
        else:
            print(f"Could not delete {filename} after retries - still in use")

    os.rmdir(dir_path)
    print("Gaze and emotion overlay visualization stopped.")