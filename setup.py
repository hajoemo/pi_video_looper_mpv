from setuptools import setup, find_packages

setup(
    name="mpv_video_looper",
    version="1.0.0",
    description="Raspberry Pi dedicated video looping application using mpv",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    author="Your Name",
    license="GPL-2.0",
    packages=find_packages(),
    python_requires=">=3.9",
    install_requires=[
        "pygame>=2.0",
    ],
    extras_require={
        "gpio": ["RPi.GPIO"],
        "mpv-binding": ["python-mpv"],
    },
    entry_points={
        "console_scripts": [
            "mpv_video_looper=mpv_video_looper.video_looper:main",
        ],
    },
)
