#!/bin/bash
# setup-controller.sh - Setup mgmt node as HPC controller

set -e

echo "Setting up HPC Controller..."

# Update system
dnf update -y

# Install required packages
dnf install -y \
    slurm-slurmdbd \
    slurm-slurmctld \
    mariadb-server \
    epel-release \
    http://repo.warewulf.org/warewulf-repo-3-1.noarch.rpm

# Configure database
systemctl enable mariadb
systemctl start mariadb

# Create HPC database
mysql -e "CREATE DATABASE IF NOT EXISTS slurm_acct_db;"
mysql -e "CREATE USER IF NOT EXISTS 'slurm'@'localhost' IDENTIFIED BY 'slurm';"
mysql -e "GRANT ALL PRIVILEGES ON slurm_acct_db.* TO 'slurm'@'localhost';"

# Configure Slurm
cp /etc/slurm/slurm.conf.example /etc/slurm/slurm.conf

# Configure Warewulf
wwctl container import docker://rockylinux:9 rockylinux9

echo "Controller setup complete!"
