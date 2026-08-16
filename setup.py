"""
setup.py — Packaging configuration for wtfix.

Install in editable/development mode:
    pip install -e .

After installation the `wtfix` command will be available system-wide.
"""

from setuptools import find_packages, setup

from wtfix import __version__, __author__, __description__

setup(
    name="wtfix",
    version=__version__,
    author=__author__,
    description=__description__,
    long_description=open("README.md", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    url="https://github.com/erenbezgin/wtfix",
    packages=find_packages(exclude=["tests*"]),
    python_requires=">=3.9",
    install_requires=[
        "typer>=0.12.0",
        "rich>=13.7.0",
        "google-genai>=1.0.0",
    ],
    entry_points={
        "console_scripts": [
            "wtfix=wtfix.cli:run",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Environment :: Console",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Software Development :: Debuggers",
        "Topic :: Utilities",
    ],
    keywords="cli ai gemini debug error fix terminal devtools",
    license="MIT",
    project_urls={
        "Bug Reports": "https://github.com/erenbezgin/wtfix/issues",
        "Source": "https://github.com/erenbezgin/wtfix",
    },
)
