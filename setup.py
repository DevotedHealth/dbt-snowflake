#!/usr/bin/env python
import os
import sys
import re

# require python 3.7 or newer
if sys.version_info < (3, 7):
    print("Error: dbt does not support this version of Python.")
    print("Please upgrade to Python 3.7 or higher.")
    sys.exit(1)


# require version of setuptools that supports find_namespace_packages
from setuptools import setup

try:
    from setuptools import find_namespace_packages
except ImportError:
    # the user has a downlevel version of setuptools.
    print("Error: dbt requires setuptools v40.1.0 or higher.")
    print(
        'Please upgrade setuptools with "pip install --upgrade setuptools" '
        "and try again"
    )
    sys.exit(1)


# pull long description from README
this_directory = os.path.abspath(os.path.dirname(__file__))
with open(os.path.join(this_directory, "README.md")) as f:
    long_description = f.read()

package_name = "dbt-snowflake"
package_version = "1.0.0dvtd"
dbt_core_version = "1.0.0"
description = """The Snowflake adapter plugin for dbt"""

setup(
    name=package_name,
    version=package_version,
    description=description,
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="dbt Labs",
    author_email="info@dbtlabs.com",
    url="https://github.com/dbt-labs/dbt-snowflake",
    packages=find_namespace_packages(include=["dbt", "dbt.*"]),
    include_package_data=True,
    install_requires=[
        "dbt-core~={}".format(dbt_core_version),
        "snowflake-connector-python[secure-local-storage]>=2.4.1,<2.8.0",
        "requests<3.0.0",
        "cryptography>=3.2,<4",
    ],
    zip_safe=False,
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "License :: OSI Approved :: Apache Software License",
        "Operating System :: Microsoft :: Windows",
        "Operating System :: MacOS :: MacOS X",
        "Operating System :: POSIX :: Linux",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
    ],
    python_requires=">=3.7",
)
