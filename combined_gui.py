"""
Simulation GUI Loader (Final Double-Clickable Version)
======================================================
This script is designed to be the heart of a portable simulation application.
It robustly finds a 'simulation' subdirectory located next to it, regardless
of where the user places the main project folder.
"""
import tkinter as tk
from tkinter.scrolledtext import ScrolledText
import threading
import io
from contextlib import redirect_stdout
import matplotlib
import traceback
from pathlib import Path

# Configure Matplotlib for Tkinter
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt

# --- Matplotlib Non-Blocking Plotting ---
def show_non_blocking(*args, **kwargs):
    kwargs.setdefault("block", False)
    if plt.get_fignums():
        plt.show_original(*args, **kwargs)
    else:
        print("Note: Script called plt.show() but no plot was open.")

plt.show_original = plt.show
plt.show = show_non_blocking


# --- Core Logic ---

def load_scripts_from_directory():
    """
    Finds a 'simulation' subdirectory next to this script and loads all
    .py files from within it. Returns a dictionary and a status message.
    """
    scripts = {}
    try:
        # This is the key: Find the directory this script is in.
        base_dir = Path(__file__).resolve().parent
        simulation_dir = base_dir / 'simulation'
        
        # Check if the 'simulation' directory actually exists.
        if not simulation_dir.is_dir():
            error_msg = (f"FATAL ERROR: The 'simulation' folder was not found.\n\n"
                         f"Please ensure the folder structure is correct:\n\n"
                         f"YourProject/\n"
                         f"├── run_simulations_gui.py\n"
                         f"└── simulation/\n"
                         f"    └── your_script.py\n\n"
                         f"Expected location: {simulation_dir}")
            return {}, error_msg

        # Load all .py files from the simulation directory.
        for script_path in simulation_dir.glob('*.py'):
            try:
                with open(script_path, 'r', encoding='utf-8') as f:
                    scripts[script_path.name] = f.read()
            except Exception as e:
                print(f"Error loading script {script_path.name}: {e}")
        
        if not scripts:
             return {}, f"Found 'simulation' folder, but it contains no .py files.\nLocation: {simulation_dir}"

        return scripts, f"Successfully loaded {len(scripts)} scripts from '{simulation_dir.name}' folder."

    except Exception as e:
        return {}, f"An unexpected error occurred while finding scripts: {e}"

def run_script(script_name, code, console_widget):
    console_widget.delete("1.0", tk.END)
    output_buffer = io.StringIO()
    with redirect_stdout(output_buffer):
        try:
            print(f"--- Running {script_name} ---\n")
            exec(code, {"__name__": "__main__"})
            print(f"\n--- {script_name} finished ---")
        except Exception:
            print(f"\n--- ERROR IN {script_name} ---")
            traceback.print_exc()

    console_widget.insert(tk.END, output_buffer.getvalue())
    console_widget.see(tk.END)


# --- Main Application GUI ---

class SimulationApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Simulation Runner")
        self.root.geometry("800x600")

        self.scripts, self.status_message = load_scripts_from_directory()
        script_names = sorted(list(self.scripts.keys()))

        top_frame = tk.Frame(self.root, padx=10, pady=5)
        top_frame.pack(fill=tk.X)

        self.selected_script = tk.StringVar()
        self.run_button = tk.Button(top_frame, text="Run Simulation", command=self.start_simulation_thread)

        if script_names:
            self.selected_script.set(script_names[0])
            self.option_menu = tk.OptionMenu(top_frame, self.selected_script, *script_names)
        else:
            self.selected_script.set("No scripts found")
            self.option_menu = tk.OptionMenu(top_frame, self.selected_script, "No scripts found")
            self.option_menu.config(state=tk.DISABLED)
            self.run_button.config(state=tk.DISABLED)

        self.option_menu.pack(side=tk.LEFT, expand=True, fill=tk.X)
        self.run_button.pack(side=tk.LEFT, padx=10)

        self.console = ScrolledText(self.root, width=100, height=30, wrap=tk.WORD, bg="black", fg="lightgray", insertbackground="white")
        self.console.pack(padx=10, pady=5, expand=True, fill=tk.BOTH)
        self.console.insert(tk.END, self.status_message)

    def start_simulation_thread(self):
        script_to_run = self.selected_script.get()
        script_code = self.scripts.get(script_to_run)
        if not script_code: return

        self.run_button.config(state=tk.DISABLED, text="Running...")
        thread = threading.Thread(target=self.run_simulation_in_background, args=(script_to_run, script_code))
        thread.daemon = True
        thread.start()

    def run_simulation_in_background(self, script_name, code):
        run_script(script_name, code, self.console)
        self.run_button.config(state=tk.NORMAL, text="Run Simulation")

if __name__ == "__main__":
    main_window = tk.Tk()
    app = SimulationApp(main_window)
    main_window.mainloop()