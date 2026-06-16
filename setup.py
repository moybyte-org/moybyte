from setuptools import find_packages, setup


setup(
    name="kidcode",
    version="0.1.0",
    description="PC-first SDK and simulator for KidCode projects.",
    packages=find_packages(include=["kidcode*", "kidcode_sim*", "kidcode_blocks*", "kidcode_cli*"]),
    python_requires=">=3.10",
    install_requires=[],
    extras_require={
        "dev": ["pytest"],
        "sim": ["pygame>=2.5"],
    },
    entry_points={
        "console_scripts": [
            "kidcode=kidcode_cli.main:main",
        ],
    },
)
