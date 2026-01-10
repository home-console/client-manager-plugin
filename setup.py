from setuptools import setup, find_packages

setup(
    name='client_manager',
    version='0.1.0',
    description='Client Manager service package',
    packages=find_packages(exclude=['client_manager', 'tests']),
    include_package_data=True,
)
