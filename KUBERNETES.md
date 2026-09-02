# ConfigSync on Kubernetes

Phase 8 deploys ConfigSync to a local Kubernetes cluster using plain manifests.

## Topology

The platform services run in the `configsync` namespace:

```text
configsync namespace

storage-a ----\
               \
                -> control-plane <- Prometheus
storage-b ----/
                      ^
                      |
        +-------------+-------------+
        |                           |
customer-a namespace          customer-b namespace
configsync-agent              configsync-agent
```

The customer agents use Kubernetes DNS to reach:

```text
http://control-plane.configsync.svc.cluster.local:8000
```

The control plane reaches storage through namespace-local Services:

```text
http://storage-a:8101
http://storage-b:8101
```

## Prerequisites

You need:

- Docker Desktop or another local Kubernetes cluster;
- `docker`;
- `kubectl`;
- a working Kubernetes context.

Check the cluster:

```powershell
kubectl config current-context
kubectl get nodes
```

If you use Docker Desktop, enable Kubernetes in Docker Desktop before continuing.

## Build the local image

From the repository root:

```powershell
docker build -t configsync:local .
```

The manifests use:

```yaml
image: configsync:local
imagePullPolicy: IfNotPresent
```

For Docker Desktop's local cluster this is intended to reuse the locally built image. If you use a different local Kubernetes distribution, load `configsync:local` into that cluster using the image-loading mechanism provided by that distribution.

## Validate the manifests

```powershell
kubectl apply --dry-run=client -f .\k8s\
```

## Deploy ConfigSync

```powershell
kubectl apply -f .\k8s\
```

Check namespaces:

```powershell
kubectl get namespaces configsync customer-a customer-b
```

Check platform resources:

```powershell
kubectl get pods,svc,pvc -n configsync
```

Check customer agents:

```powershell
kubectl get pods,pvc -n customer-a
kubectl get pods,pvc -n customer-b
```

Wait for the platform Deployments:

```powershell
kubectl rollout status deployment/storage-a -n configsync
kubectl rollout status deployment/storage-b -n configsync
kubectl rollout status deployment/control-plane -n configsync
kubectl rollout status deployment/prometheus -n configsync
```

Wait for the agents:

```powershell
kubectl rollout status deployment/configsync-agent -n customer-a
kubectl rollout status deployment/configsync-agent -n customer-b
```

## Access the control plane

The services remain `ClusterIP`; use port forwarding for local access.

Control plane:

```powershell
kubectl port-forward service/control-plane 8000:8000 -n configsync
```

Then open:

```text
http://127.0.0.1:8000/docs
http://127.0.0.1:8000/metrics
```

Prometheus, in another terminal:

```powershell
kubectl port-forward service/prometheus 9090:9090 -n configsync
```

Then open:

```text
http://127.0.0.1:9090
```

## Successful rollout demo

With the control-plane port-forward running, create version 1:

```powershell
$body = @{
    content = @{
        service = "firewall"
        port = 443
        enabled = $true
    }
} | ConvertTo-Json

Invoke-RestMethod `
    -Method Post `
    -Uri http://127.0.0.1:8000/configs/firewall `
    -ContentType application/json `
    -Body $body
```

Both agents should reconcile to version 1.

Check status:

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/customers/customer-a/configs/firewall/status
Invoke-RestMethod -Uri http://127.0.0.1:8000/customers/customer-b/configs/firewall/status
```

Create candidate version 2:

```powershell
$updatedBody = @{
    content = @{
        service = "firewall"
        port = 8443
        enabled = $true
    }
} | ConvertTo-Json

Invoke-RestMethod `
    -Method Put `
    -Uri http://127.0.0.1:8000/configs/firewall `
    -Headers @{ "If-Match" = "1" } `
    -ContentType application/json `
    -Body $updatedBody
```

Start the canary rollout:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/rollouts/firewall
```

`customer-a` receives version 2 first. `customer-b` receives it only after the canary reports `synced`.

## Kubernetes failure demo

Delete the first storage pod:

```powershell
kubectl delete pod -l app=storage-a -n configsync
```

The Deployment recreates the pod automatically. During the failure window, a configuration read can use storage B instead.

You can inspect the platform at any time:

```powershell
kubectl get pods -n configsync -w
```

Check the storage failure metric after a read attempts the unavailable replica:

```text
storage_node_errors_total
```

## Agent recovery demo

Delete the customer-a agent pod:

```powershell
kubectl delete pod -l app=configsync-agent -n customer-a
```

The Deployment creates a replacement. Because the agent state is on a PVC, the replacement starts with the previously applied local state and continues reconciliation.

## Logs

Control plane:

```powershell
kubectl logs deployment/control-plane -n configsync
```

Storage:

```powershell
kubectl logs deployment/storage-a -n configsync
kubectl logs deployment/storage-b -n configsync
```

Agents:

```powershell
kubectl logs deployment/configsync-agent -n customer-a
kubectl logs deployment/configsync-agent -n customer-b
```

## Remove the deployment

Delete workloads and namespaces:

```powershell
kubectl delete -f .\k8s\
```

Deleting the namespaces also removes their namespaced PVC objects and associated local-cluster storage according to the cluster's storage policy.

## What this phase demonstrates

The Kubernetes deployment demonstrates:

- namespace isolation between platform and customer environments;
- service discovery through Kubernetes DNS;
- Deployment-based self-healing;
- readiness and liveness probes;
- persistent state with PVCs;
- independent customer reconciliation agents;
- Prometheus monitoring inside the cluster;
- the same canary rollout and rollback semantics that work under Docker Compose.

This remains a local educational deployment. It intentionally does not add ingress, TLS, cloud load balancers, operators, service meshes, authentication, or managed cloud infrastructure.
