import PyInstaller.__main__
import shutil
import os
import time
from pathlib import Path
import certifi

def remove_if_exists(path):
    if path.exists():
        try:
            if path.is_file():
                path.unlink()
            else:
                shutil.rmtree(path)
        except PermissionError:
            print(f"Waiting for {path} to be released...")
            time.sleep(2)  # Wait a bit and try again
            try:
                if path.is_file():
                    path.unlink()
                else:
                    shutil.rmtree(path)
            except Exception as e:
                print(f"Could not remove {path}: {e}")
                print("Please close any running instances and try again.")
                exit(1)

# Clean previous builds
for path in ['build', 'dist']:
    remove_if_exists(Path(path))

# Remove existing .spec files
for spec in Path('.').glob('*.spec'):
    remove_if_exists(spec)

# Common options
common_options = [
    '--noconfirm',
    '--clean',
    '--distpath', './output',
    '--onefile',
    '--hidden-import=asyncio',
    '--hidden-import=bleak',
    '--hidden-import=pusher',
    '--hidden-import=pysher',
    '--hidden-import=certifi',
    '--hidden-import=src.data_parser',
    '--hidden-import=src.bluetooth_manager',
    '--add-data', f"{certifi.where()};certifi"
]

try:
    print("\n=== Building Configuration Tool ===")
    PyInstaller.__main__.run([
        'configure.py',
        '--name=berry-monitor-config',
        '--log-level=DEBUG',
        *common_options
    ])

    print("\n=== Building Main Application ===")
    dist_path = Path(__file__).parent / 'dist'
    
    # Clean the directory first
    if dist_path.exists():
        shutil.rmtree(dist_path)
    
    # Create fresh directory
    dist_path.mkdir(parents=True)
    
    print(f"Building to: {dist_path.absolute()}")
    
    PyInstaller.__main__.run([
        'app.py',
        '--name=berry-monitor',
        '--log-level=DEBUG',
        '--distpath', str(dist_path.absolute()),
        '--workpath', 'build',
        '--clean',
        *common_options
    ])

    # Verify build result
    exe_path = dist_path / 'berry-monitor'
    if not exe_path.exists():
        print(f"\nERROR: Failed to create executable at {exe_path}")
        exit(1)
    else:
        print(f"\nSuccessfully built: {exe_path}")
        
        # Use ~/.local/bin instead of Desktop for Linux
        local_bin = Path.home() / '.local' / 'bin'
        if not local_bin.exists():
            local_bin.mkdir(parents=True)
        
        try:
            shutil.copy2(exe_path, local_bin / 'berry-monitor')
            # Make the file executable
            os.chmod(local_bin / 'berry-monitor', 0o755)
            print(f"Copied to: {local_bin}")
            print("Make sure ~/.local/bin is in your PATH")
        except Exception as e:
            print(f"Warning: Could not copy to {local_bin}: {e}")
            print(f"Executable is still available at: {exe_path}")

except Exception as e:
    print(f"\nBuild failed: {e}")
    exit(1)

print("\nBuild completed successfully!") 