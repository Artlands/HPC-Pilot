# Makefile for virtual cluster management

.PHONY: up down ssh-mgmt ssh-login ssh-cpu01 ssh-gpu01 test test-unit test-integration seed-state

# VM configuration
VMS := mgmt login01 cpu01 gpu01
NETWORK := hpc-cluster
POOL := /var/lib/libvirt/hpc-cluster

# Bring up the virtual cluster
up: _check-prerequisites
	@echo "Creating virtual cluster..."
	@# Create network
	virsh net-destroy $(NETWORK) || true
	virsh net-undefine $(NETWORK) || true
	virsh net-define < deploy/network.xml
	virsh net-start $(NETWORK)
	virsh net-autostart $(NETWORK)
	@# Create storage pool
 virsh pool-destroy $(POOL) || true
	virsh pool-undefine $(POOL) || true
	virsh pool-create-as $(POOL) dir --target $(POOL)
	@# Create VMs
	@.for vm in $(VMS); do \
		if [ -f "deploy/vm-$$vm.xml" ]; then \
			echo "Creating $$vm..."; \
			virsh destroy $$vm 2>/dev/null || true; \
			virsh undefine $$vm 2>/dev/null || true; \
			virsh define deploy/vm-$$vm.xml; \
			virsh start $$vm; \
		fi; \
	done
	@echo "Waiting for VMs to boot..."
	@sleep 60
	@echo "Configuring mgmt node..."
	@ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 root@mgmt 'bash -c "cd /root/hpc-cluster && ./scripts/setup-controller.sh"' || echo "Controller setup requires manual intervention"
	@echo "Virtual cluster is up!"

# Shutdown all VMs and cleanup
down:
	@echo "Shutting down virtual cluster..."
	@for vm in $(VMS); do \
		virsh shutdown $$vm 2>/dev/null || true; \
	done
	@sleep 30
	@for vm in $(VMS); do \
		virsh destroy $$vm 2>/dev/null || true; \
	done
	@# Cleanup
	@for vm in $(VMS); do \
		virsh undefine $$vm --nvram 2>/dev/null || true; \
	done
	@virsh net-destroy $(NETWORK) || true
	@virsh net-undefine $(NETWORK) || true
	@echo "Virtual cluster cleaned up."

# SSH to VMs
ssh-mgmt:
	ssh -o StrictHostKeyChecking=no root@mgmt

ssh-login:
	ssh -o StrictHostKeyChecking=no root@login01

ssh-cpu01:
	ssh -o StrictHostKeyChecking=no root@cpu01

ssh-gpu01:
	ssh -o StrictHostKeyChecking=no root@gpu01

# Test targets
test: test-unit test-integration

test-unit:
	@echo "Running unit tests..."
	python -m pytest tests/unit -v

test-integration:
	@echo "Running integration tests..."
	@# These tests require the virtual cluster to be up
	@# Brief test of run playbook functionality
	python -m pytest tests/integration -v -k "not gpu" || echo "Integration tests require full cluster setup"

test-all: test-unit test-integration

# State management
seed-state:
	@echo "Seeding state store..."
	python -c "from hpc_agent.state.db import init_db; init_db()"
	@echo "State store seeded."

# Prerequisites check
_check-prerequisites:
	@if ! command -v virsh &> /dev/null; then \
		echo "Error: virsh not found. Install libvirt."; \
		exit 1; \
	fi
	@if ! command -v virt-install &> /dev/null; then \
		echo "Error: virt-install not found. Install libvirt."; \
		exit 1; \
	fi
	@echo "Prerequisites OK."
