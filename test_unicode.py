#!/usr/bin/env python3
import sys
import time

def play():
    # Enable ANSI support on Windows
    if sys.platform == 'win32':
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    
    # Reconfigure stdout for UTF-8
    sys.stdout.reconfigure(encoding='utf-8')
    
    # Hide cursor, switch to alternate screen, clear
    sys.stdout.write('[?25l[?1049h[2J')
    sys.stdout.flush()
    
    try:
        # Display a simple geometric pattern
        for i in range(3):
            sys.stdout.write('[5;10H')  # Move to row 5, col 10
            sys.stdout.write('●◆▪▫◇○■□')  # Geometric characters
            sys.stdout.flush()
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        # Restore screen and cursor
        sys.stdout.write('[?1049l[?25h')
        sys.stdout.flush()

if __name__ == '__main__':
    play()
