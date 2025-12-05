from setuptools import setup, find_packages
# To use a consistent encoding
from codecs import open

REQUIREMENTS = [i.strip() for i in open("requirements.txt").readlines()]

setup(
    name='scflows',
    version='0.2.0',
    packages = find_packages(),
    include_package_data=True,
    install_requires=[REQUIREMENTS],
    zip_safe=False
)