__all__ = ["BaseExclusion", "CircularExclusion"]

from bapsf_motion.motion_list.exclusions.base import BaseExclusion
from bapsf_motion.motion_list.exclusions.circular import CircularExclusion

# TODO: types of exclusions
#       - Divider (greater/less than a dividing line)
#       - Port (an LaPD port)
#       - LaPD (a full LaPD setup)
#       - Shadow (specialty to shadow from a given point)
#       - Rectangular
#       - Cylindrical
#       - Sphere
#       - Polygon
