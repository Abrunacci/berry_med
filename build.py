import PyInstaller.__main__
import shutil
import os
import time
from pathlib import Path

def remove_if_exists(path):
    """Remove a file or directory if it exists."""
    if path.exists():
        try:
            if path.is_file():
                path.unlink()
            else:
                shutil.rmtree(path)
        except PermissionError:
            print(f"Waiting for {path} to be released...")
            time.sleep(2)  # Wait and retry
            try:
                if path.is_file():
                    path.unlink()
                else:
                    shutil.rmtree(path)
            except Exception as e:
                print(f"Could not remove {path}: {e}")
                print("Please close any running instances and try again.")
                exit(1)

def setup_directory(directory):
    """Ensure the directory exists and is clean."""
    if directory.exists():
        remove_if_exists(directory)
    directory.mkdir(parents=True, exist_ok=True)

def run_pyinstaller(script, name, dist_path, work_path, options):
    """Run PyInstaller for a given script."""
    try:
        print(f"\n=== Building {name} ===")
        PyInstaller.__main__.run([
            script,
            f'--name={name}',
            f'--distpath={dist_path}',
            f'--workpath={work_path}',
            *options
        ])
        exe_path = Path(dist_path) / name
        if not exe_path.exists():
            raise FileNotFoundError(f"Executable not found at {exe_path}")
        print(f"Successfully built: {exe_path}")
        return exe_path
    except Exception as e:
        print(f"\nERROR: Failed to build {name}: {e}")
        exit(1)

def copy_executable_to_bin(exe_path, bin_dir):
    """Copy the executable to a local bin directory."""
    try:
        if not bin_dir.exists():
            bin_dir.mkdir(parents=True)
        shutil.copy2(exe_path, bin_dir / exe_path.name)
        os.chmod(bin_dir / exe_path.name, 0o755)
        print(f"Copied to: {bin_dir}")
        print("Make sure ~/.local/bin is in your PATH")
    except Exception as e:
        print(f"Warning: Could not copy to {bin_dir}: {e}")
        print(f"Executable is still available at: {exe_path}")

if __name__ == "__main__":
    # Paths and options
    dist_path_main = Path('./output')  # Main output directory
    dist_path_individual = Path('./dist')  # Per-project directory
    work_path = Path('./build')  # Temporary working directory
    spec_files = Path('.').glob('*.spec')
    local_bin = Path.home() / '.local' / 'bin'
    
    # Common PyInstaller options
    common_options = [
        '--noconfirm',
        '--clean',
        '--onefile',
        '--hidden-import=asyncio',
        '--hidden-import=bleak',
        '--hidden-import=pusher',
        '--hidden-import=src.data_parser',
        '--hidden-import=src.bluetooth_manager',
        '--log-level=DEBUG',
    ]
    
    # Clean up previous builds
    print("\n=== Cleaning previous builds ===")
    for path in [dist_path_main, dist_path_individual, work_path]:
        remove_if_exists(path)
    for spec in spec_files:
        remove_if_exists(spec)
    
    # Build Configuration Tool
    config_tool_path = run_pyinstaller(
        script='configure.py',
        name='berry-monitor-config',
        dist_path=dist_path_main,
        work_path=work_path,
        options=common_options
    )
    
    # Build Main Application
    setup_directory(dist_path_individual)
    app_exe_path = run_pyinstaller(
        script='app.py',
        name='berry-monitor',
        dist_path=dist_path_individual,
        work_path=work_path,
        options=common_options
    )
    
    # Copy executable to ~/.local/bin
    copy_executable_to_bin(app_exe_path, local_bin)
    
    print("\nBuild completed successfully!")
