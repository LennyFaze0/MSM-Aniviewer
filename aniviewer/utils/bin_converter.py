"""
BIN Converter
Utilities for converting .bin files to .json using the bin2json script
"""

import sys
import os
import subprocess
import runpy
import contextlib
from pathlib import Path
from typing import Optional, Tuple


def find_bin2json_script() -> Optional[str]:
    """
    Find the bin2json script in the Resources folder
    
    Returns:
        Path to bin2json script if found, None otherwise
    """
    # Try to find relative to this file
    current_dir = Path(__file__).parent.parent
    bin2json_path = current_dir / "Resources" / "bin2json" / "rev6-2-json.py"
    
    if bin2json_path.exists():
        return str(bin2json_path)
    
    return None


def convert_bin_to_json(bin_path: str, bin2json_script: str) -> Tuple[bool, str]:
    """
    Convert a .bin file to .json using the bin2json script
    
    Args:
        bin_path: Path to the .bin file
        bin2json_script: Path to the bin2json script
    
    Returns:
        Tuple of (success: bool, message: str)
    """
    try:
        if getattr(sys, "frozen", False):
            previous_cwd = os.getcwd()
            previous_argv = sys.argv[:]
            script_dir = os.path.dirname(bin2json_script)
            inserted_path = False
            try:
                if script_dir:
                    os.chdir(script_dir)
                sys.argv = [bin2json_script, "d", bin_path]
                if script_dir and script_dir not in sys.path:
                    sys.path.insert(0, script_dir)
                    inserted_path = True
                runpy.run_path(bin2json_script, run_name="__main__")
                return True, "Conversion successful"
            except SystemExit as exc:
                code = exc.code
                if isinstance(code, int) and code != 0:
                    return False, f"Conversion failed: {code}"
                if isinstance(code, str) and code.strip():
                    return False, f"Conversion failed: {code}"
                return True, "Conversion successful"
            finally:
                sys.argv = previous_argv
                os.chdir(previous_cwd)
                if inserted_path:
                    with contextlib.suppress(ValueError):
                        sys.path.remove(script_dir)
        # Run bin2json script
        result = subprocess.run(
            [sys.executable, bin2json_script, 'd', bin_path],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(bin2json_script)
        )

        if result.returncode == 0:
            return True, "Conversion successful"
        else:
            return False, f"Conversion failed: {result.stderr}"

    except Exception as e:
        return False, f"Error during conversion: {e}"
