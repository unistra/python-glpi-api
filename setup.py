# coding: utf-8

from setuptools import setup

setup(
    name='glpi-api',
    version='0.1.0',
    author='François Ménabé',
    author_email='francois.menabe@unistra.fr',
    url='https://git.unistra.fr/di/glpi/python-glpi-api',
    download_url='https://git.unistra.fr/di/ansible/roles/glpi/deploy/tags/',
    license='GPLv3+',
    description='Wrap calls to GLPI REST API.',
    long_description=open('README.rst').read(),
    keywords=['rest', 'api', 'glpi'],
    classifiers=[
        'License :: OSI Approved :: GNU Lesser General Public License v3 or later (LGPLv3+)',
        'Development Status :: 4 - Beta',
        'Programming Language :: Python :: 2.7',
        'Programming Language :: Python :: 3'
    ],
    py_modules=['glpi_api'],
    install_requires=['requests']
)
