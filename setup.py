# coding=utf-8
from setuptools import find_packages, setup

setup(
    name="OctoPrint-FilamentGuard",
    version="0.1.0",
    description=(
        "Filament jam/runout detection using a hall-effect pulse sensor, "
        "with no-motion and under-extrusion (partial clog) detection modes."
    ),
    author="Ionut Poroh",
    author_email="ionut.poroh@gmail.com",
    url="https://github.com/ionutporoh/OctoPrint-FilamentGuard",
    license="AGPLv3",
    packages=find_packages(exclude=["tests"]),
    package_data={
        "octoprint_filamentguard": ["templates/*.jinja2", "static/js/*.js"]
    },
    include_package_data=True,
    install_requires=["gpiod>=2.0"],
    python_requires=">=3.7,<4",
    entry_points={
        "octoprint.plugin": ["filamentguard = octoprint_filamentguard"]
    },
    zip_safe=False,
)
