#!/usr/bin/env python3
import base64
import os

# Base64 encoded 16x16 microphone icon with transparent background
mic_icon_16 = """
iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAABHNCSVQICAgIfAhkiAAAAAlwSFlzAAAB
uwAAAbsBOuzj4gAAABl0RVh0U29mdHdhcmUAd3d3Lmlua3NjYXBlLm9yZ5vuPBoAAAGDSURBVDiNpZK9
SgNBFIXPnZnNZpPdmE0kYhERJIUPYC9oYSNoYSGCjY1gY2FhYeELWFiIhYVgYxEQFAtBwUoRxB8kZjf7
k9mZsbAJu0kE8cJwZ+7cc+6ZM5fJsoy+Q0T9GgCYJEnOPM+7SdP0WCk1EgTBSRzHy0qpESHEdhAEp0KI
HSHEdhiGZ0KIXdd1b+M4XlJKjfq+f5ym6YlS6oPBGFuzbXs1DMNzAGCMrXPO1wkhG4yxDULIOiHkJ+ec
b3HONznnm4SQTc75FiHkO+d8W0q5K6XcA4Asy/ZM07zQdf3QMIznIAjOAUBKuW+a5qVhGE+6rj8CQJZl
+6ZpXhiG8Qj0CmRZdqjr+pNlWVdSygMASJLkzLbtG9M0L5MkOQWAJEnObNu+tSzrOkmSM/SvQAixZ1nW
tWVZN0KIPQDwPO/Otu1by7JuPM+7AwDP8+5s277tCe5/CzDGVizLujIMY5Extloul8crlcoEY2ylWq1O
ViqVccbYaqlUGvsvAQAQQuwbhnFULBaHOOcb+Xx+KJfLDeVyuaFCoTBUKBSGfgBCwYdXVnKPFQAAAABJ
RU5ErkJggg==
"""

# Base64 encoded 32x32 microphone icon with transparent background
mic_icon_32 = """
iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAABHNCSVQICAgIfAhkiAAAAAlwSFlzAAAD
EwAAAxMBPWaDxwAAABl0RVh0U29mdHdhcmUAd3d3Lmlua3NjYXBlLm9yZ5vuPBoAAAKYSURBVFiF7ZZN
aBNBFMd/M5tks9lN0iZpbT5qwYMXQfDkQbyJFx/ixYsXvXgQQcSLiIiIiHgQEQ+CiHjw4smDIIIXQbyI
iIiIiHgQRKQ0zW6y2WSz2d2ZeQezSZs0Tdqk4sEHj535mHn/9+bNmxmltWYxTVtU9P8AixrAXmgAIcQh
4BDQBbQBFmADWSAFvAIGlVJP/9QHhBCHgZNAELgHvARGgQlAAx5gJbAZ2AEMAMeVUvf/CEAI0QVcAHYC
14HzSqmxOmM84BBwFtgF9Cul7v4WgBBiO3ALKAAnlFKDDSZ8FegDjiqlbjQFIITYAAwBk8AepdTHJpMH
OA5cAXYrpYbqAQghhA8YBkaBA0qpXDPJ/2RngOvAkFLK/3OHXmXMZeA9cPBvJA/QC3wALlcVqHrgDJAG
9imlJpsJKoRYBrQCHmAamNJaF5qZRyn1SQixF3gGnBVC9FdqUAHYD9xWSk3PF0wI4QBHgD1AN+BWugrA
e+AecEUp9W6+eUqpCSHEbeAAcAi4WQHoBp7XCyKEWA1cA7YABvCYYhFJAhFgHcXW2wZsE0KcAo4ppXJ1
pj9Lsd13lwE8QKYOwFrgIcWWOqWUuvhLvwQGhBCXgAvAJSHEjFLqTI35GWClEMJWSrlljxSqDTKAJ8Bm
oF8pdaXaIKXUlBDiKPABGBBC3FVKvaoxPQN4AQfIlwEKVQYY5Zt6vNbkFVNKTQshhigWqUGKnlJtBcAr
X4OyBzJVBhjABLCikcRlG6f4+lbXCFkGcMsAk1UGGEAc2NRg8s0Uj2O8zjgDSJQB3gArGwQIU/SSeJ1x
BvCpDPCc4m1Wd4MQQnQAG4HndcZ9BF6XAQaB3iYAesvz65mmlEoKIR5R3KbfbMFvRnmx/4hWLPqfkR8A
8hNGZkpfWJIAAAAASUVORK5CYII=
"""

def save_icon(base64_data, filename):
    """Save base64 encoded image data to a file"""
    # Remove any whitespace and newlines from the base64 string
    base64_data = ''.join(base64_data.split())
    
    # Decode the base64 data
    image_data = base64.b64decode(base64_data)
    
    # Write the binary data to a file
    with open(filename, 'wb') as f:
        f.write(image_data)
    
    print(f"Created icon: {filename}")

if __name__ == "__main__":
    # Get the directory where this script is located
    script_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Save the icons
    save_icon(mic_icon_16, os.path.join(script_dir, "mic_16.png"))
    save_icon(mic_icon_32, os.path.join(script_dir, "mic_32.png"))
    
    print("Icons created successfully!")
