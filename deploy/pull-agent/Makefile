# Build the pull agent image and the install packages.
#
#   make image      build zk-pull-agent:<version> for this machine
#   make pkg        all packages into dist/ (Docker amd64 + arm64, plain Python zip)
#   make test       run the image's --help and a pull against an unreachable device
#   make clean

ROOT     := $(abspath ../..)
VERSION  := $(shell sed -n 's/^__version__ = "\(.*\)"/\1/p' $(ROOT)/zk_push/__init__.py)
IMAGE    := zk-pull-agent
DIST     := $(ROOT)/dist
ARCHS    := amd64 arm64
BUNDLE_FILES := compose.yaml .env.example INSTALL.md

.PHONY: image pkg pkg-docker pkg-python test clean $(addprefix pkg-,$(ARCHS))

image:
	docker build -f Dockerfile --build-arg VERSION=$(VERSION) -t $(IMAGE):$(VERSION) -t $(IMAGE):latest $(ROOT)

pkg: pkg-docker pkg-python
	@cd $(DIST) && sha256sum zk-pull-agent-$(VERSION)-* > SHA256SUMS && ls -lh && cat SHA256SUMS

pkg-docker: $(addprefix pkg-,$(ARCHS))

# Docker bundle per architecture: image.tar.gz + compose.yaml + .env.example + INSTALL.md
$(addprefix pkg-,$(ARCHS)): pkg-%:
	docker buildx build --platform linux/$* --load -f Dockerfile --build-arg VERSION=$(VERSION) \
		-t $(IMAGE):$(VERSION)-$* $(ROOT)
	rm -rf $(DIST)/$(IMAGE)-$(VERSION)-$* && mkdir -p $(DIST)/$(IMAGE)-$(VERSION)-$*
	docker save $(IMAGE):$(VERSION)-$* | gzip > $(DIST)/$(IMAGE)-$(VERSION)-$*/image.tar.gz
	cp $(BUNDLE_FILES) $(DIST)/$(IMAGE)-$(VERSION)-$*/
	sed -i 's/^ZK_PULL_TAG=.*/ZK_PULL_TAG=$(VERSION)-$*/' $(DIST)/$(IMAGE)-$(VERSION)-$*/.env.example
	tar -C $(DIST) -czf $(DIST)/$(IMAGE)-$(VERSION)-$*.tar.gz $(IMAGE)-$(VERSION)-$*
	rm -rf $(DIST)/$(IMAGE)-$(VERSION)-$*

# Plain Python bundle, for machines without Docker (e.g. Windows + Task Scheduler)
pkg-python:
	rm -rf $(DIST)/$(IMAGE)-$(VERSION)-python && mkdir -p $(DIST)/$(IMAGE)-$(VERSION)-python
	cp $(ROOT)/tools/pull_from_device.py $(ROOT)/tools/backfill_from_device.py \
		$(ROOT)/tools/export_device_users.py requirements.txt INSTALL.md $(DIST)/$(IMAGE)-$(VERSION)-python/
	cd $(DIST) && rm -f $(IMAGE)-$(VERSION)-python.zip && zip -qr $(IMAGE)-$(VERSION)-python.zip $(IMAGE)-$(VERSION)-python
	rm -rf $(DIST)/$(IMAGE)-$(VERSION)-python

test: image
	docker run --rm $(IMAGE):$(VERSION) --help > /dev/null
	@out=$$(docker run --rm $(IMAGE):$(VERSION) 192.0.2.1 https://erp.example.com --once 2>&1); \
	code=$$?; echo "$$out"; test $$code -eq 1 && echo "$$out" | grep -q "Pull failed" && echo "test: OK"

clean:
	rm -rf $(DIST)/$(IMAGE)-*
