"""
BIN Converter
Utilities for converting .bin files to .json using the bin2json script
"""

import sys
import os
import runpy
import contextlib
import io
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
        previous_cwd = os.getcwd()
        previous_argv = sys.argv[:]
        script_dir = os.path.dirname(bin2json_script)
        inserted_path = False
        stdout_buffer = io.StringIO()
        stderr_buffer = io.StringIO()
        try:
            if script_dir:
                os.chdir(script_dir)
            sys.argv = [bin2json_script, "d", bin_path]
            if script_dir and script_dir not in sys.path:
                sys.path.insert(0, script_dir)
                inserted_path = True
            with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
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

    except Exception as e:
        return False, f"Error during conversion: {e}"
