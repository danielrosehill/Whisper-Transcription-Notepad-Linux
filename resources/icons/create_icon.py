#!/usr/bin/env python3
from PyQt5.QtGui import QPainter, QColor, QIcon, QPixmap, QPen
from PyQt5.QtCore import Qt, QRect, QSize

# Create a microphone icon
def create_microphone_icon(size=64, color=QColor(77, 77, 77)):
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    
    # Set pen and brush
    pen = QPen(color)
    pen.setWidth(2)
    painter.setPen(pen)
    painter.setBrush(color)
    
    # Draw microphone body
    mic_width = size * 0.3
    mic_height = size * 0.45
    mic_x = (size - mic_width) / 2
    mic_y = size * 0.15
    painter.drawRoundedRect(mic_x, mic_y, mic_width, mic_height, mic_width * 0.3, mic_width * 0.3)
    
    # Draw microphone stand
    stand_width = size * 0.5
    stand_height = size * 0.2
    stand_x = (size - stand_width) / 2
    stand_y = mic_y + mic_height
    painter.drawRect(stand_x + stand_width * 0.4, stand_y, stand_width * 0.2, stand_height)
    painter.drawRoundedRect(stand_x, stand_y + stand_height, stand_width, size * 0.05, size * 0.02, size * 0.02)
    
    painter.end()
    
    # Save the pixmap as a PNG file
    pixmap.save("microphone.png")
    
    # Also create smaller versions for system tray
    for small_size in [16, 24, 32]:
        small_pixmap = pixmap.scaled(small_size, small_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        small_pixmap.save(f"microphone_{small_size}.png")

if __name__ == "__main__":
    create_microphone_icon()
    print("Icons created successfully!")
