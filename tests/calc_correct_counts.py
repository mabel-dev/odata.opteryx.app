#!/usr/bin/env python
"""Calculate correct filter counts for test battery."""

# Planets data from the SQL file
masses = [0.33, 4.87, 5.97, 0.642, 1898, 568, 86.8, 102, 0.0146]
diameters = [3832, 9496, 12742, 6779, 139820, 116460, 50724, 49528, 2376]
distances = [57.9, 108.2, 149.6, 227.9, 778.5, 1434, 2873, 4495, 5913]
temps = [430, 465, 288, 223, 110, 140, 195, 200, 50]
moons = [0, 0, 1, 2, 95, 146, 27, 30, 5]

print("Filter Results:")
print(f"mass gt 100: {sum(1 for m in masses if m > 100)}")
print(f"mass lt 10: {sum(1 for m in masses if m < 10)}")
print(f"diameter gt 10000: {sum(1 for d in diameters if d > 10000)}")
print(f"distance_from_sun lt 200: {sum(1 for d in distances if d < 200)}")
print(f"distance_from_sun lt 300: {sum(1 for d in distances if d < 300)}")
print(f"distance_from_sun lt 500: {sum(1 for d in distances if d < 500)}")
print(f"mean_temperature ge 0: {sum(1 for t in temps if t >= 0)}")
print(f"mean_temperature lt 200: {sum(1 for t in temps if t < 200)}")
print(f"mean_temperature gt 0: {sum(1 for t in temps if t > 0)}")
print(f"mean_temperature gt 100: {sum(1 for t in temps if t > 100)}")
print(f"mean_temperature gt 100 or lt 200: {sum(1 for t in temps if t > 100 or t < 200)}")
print(f"mean_temperature gt 400: {sum(1 for t in temps if t > 400)}")
print(f"mean_temperature gt 400 or lt 200: {sum(1 for t in temps if t > 400 or t < 200)}")
print(f"number_of_moons le 2: {sum(1 for m in moons if m <= 2)}")
print(f"number_of_moons eq 0: {sum(1 for m in moons if m == 0)}")
print(f"number_of_moons ne 0: {sum(1 for m in moons if m != 0)}")
print(f"number_of_moons gt 0: {sum(1 for m in moons if m > 0)}")
print(
    f"number_of_moons gt 0 and mass gt 50: {sum(1 for i in range(len(moons)) if moons[i] > 0 and masses[i] > 50)}"
)
print(
    f"number_of_moons gt 0 or mass gt 100: {sum(1 for i in range(len(moons)) if moons[i] > 0 or masses[i] > 100)}"
)
print(
    f"distance_from_sun lt 500 and number_of_moons eq 0: {sum(1 for i in range(len(distances)) if distances[i] < 500 and moons[i] == 0)}"
)
