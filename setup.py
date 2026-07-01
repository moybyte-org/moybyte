from setuptools import find_packages, setup


setup(
    name="moybyte",
    version="0.1.0",
    description="PC-first SDK and simulator for Moybyte projects.",
    packages=find_packages(include=["moybyte*", "moybyte_sim*", "moybyte_blocks*", "moybyte_cli*"]),
    python_requires=">=3.10",
    install_requires=[],
    extras_require={
        "dev": ["pytest"],
        "sim": ["pygame>=2.5"],
    },
    entry_points={
        "console_scripts": [
            "moybyte=moybyte_cli.main:main",
        ],
    },
)
