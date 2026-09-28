import math
import random

NAMES = ["Steady", "Pulse", "Wave", "Heartbeat", "Escalate", "Random"]


def wobble(t):
    step = int(t / 0.4)
    a = random.Random(step).random()
    b = random.Random(step + 1).random()
    f = t / 0.4 - step
    f = f * f * (3 - 2 * f)
    return a + (b - a) * f


def level(pattern, intensity, t):
    if pattern == 1:
        shape = 1.0 if t % 1.0 < 0.5 else 0.0
    elif pattern == 2:
        shape = 0.5 - 0.5 * math.cos(2 * math.pi * t / 4.0)
    elif pattern == 3:
        p = t % 1.2
        shape = 1.0 if p < 0.12 or 0.25 <= p < 0.37 else 0.0
    elif pattern == 4:
        shape = t % 10.0 / 10.0
    elif pattern == 5:
        shape = wobble(t)
    else:
        shape = 1.0
    return max(0.0, min(1.0, intensity * shape))
