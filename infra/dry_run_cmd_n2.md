============================================
Azure AKS Sandbox Orchestrator Deployment
============================================
Resource Group: sandboxd16n2-orchestrator-rg
Location: westus3
Cluster Name: sandboxd16n2-aks
ACR Name: sandboxd16n2acr1775255475
============================================

[DRY RUN MODE] Skipping login check

Creating resource group: sandboxd16n2-orchestrator-rg
>>> az group create --name sandboxd16n2-orchestrator-rg --location westus3 --tags purpose=sandboxd16n2-orchestrator managed-by=script
✓ Resource group created

Creating Log Analytics workspace: sandboxd16n2-logs
>>> az monitor log-analytics workspace create --resource-group sandboxd16n2-orchestrator-rg --workspace-name sandboxd16n2-logs --location westus3
✓ Log Analytics workspace created

ID = /subscriptions/2cd190bb-b42a-477c-b1bb-2f20932d8dc5/resourceGroups/sandboxd16n2-orchestrator-rg/providers/Microsoft.OperationalInsights/workspaces/sandboxd16n2-logs

Creating Azure Container Registry: sandboxd16n2acr1775255475
>>> az acr create --resource-group sandboxd16n2-orchestrator-rg --name sandboxd16n2acr1775255475 --sku Premium --location westus3 --admin-enabled false
✓ ACR created

Creating AKS cluster: sandboxd16n2-aks
This may take 10-15 minutes...
>>> az aks create --resource-group sandboxd16n2-orchestrator-rg --name sandboxd16n2-aks --location westus3 --kubernetes-version 1.32 --tier premium --node-count 1 --node-vm-size Standard_D16ads_v7 --nodepool-name sys --nodepool-labels pool=system --enable-cluster-autoscaler --min-count 1 --max-count 1 --cluster-autoscaler-profile ignore-daemonsets-utilization=true scale-down-delay-after-add=20m scale-down-unneeded-time=20m --network-plugin azure --network-policy calico --enable-managed-identity --attach-acr sandboxd16n2acr1775255475 --enable-addons monitoring --workspace-resource-id /subscriptions/2cd190bb-b42a-477c-b1bb-2f20932d8dc5/resourceGroups/sandboxd16n2-orchestrator-rg/providers/Microsoft.OperationalInsights/workspaces/sandboxd16n2-logs --generate-ssh-keys --tags purpose=sandboxd16n2-orchestrator
✓ AKS cluster created with system node pool

Adding sandbox node pool...
>>> az aks nodepool add --resource-group sandboxd16n2-orchestrator-rg --cluster-name sandboxd16n2-aks --name sbx --node-count 1 --node-vm-size Standard_D16ads_v7 --labels workload=sandbox pool=sandbox --enable-cluster-autoscaler --min-count 1 --max-count 50 --node-taints workload=sandbox:NoSchedule
✓ Sandbox node pool added

Getting AKS credentials...
>>> az aks get-credentials --resource-group sandboxd16n2-orchestrator-rg --name sandboxd16n2-aks --overwrite-existing
✓ Credentials configured

Patching calico-typha to tolerate sandbox node taint...
>>> kubectl -n calico-system patch deployment calico-typha --type=strategic -p '{
  "spec": {
    "template": {
      "spec": {
        "tolerations": [
          {
            "key": "workload",
            "value": "sandbox",
            "effect": "NoSchedule"
          }
        ]
      }
    }
  }
}'
✓ calico-typha patched

Verifying cluster access...
>>> kubectl cluster-info

>>> kubectl get nodes

============================================
Deployment Complete!
============================================

Resource Group: sandboxd16n2-orchestrator-rg
AKS Cluster: sandboxd16n2-aks
ACR Name: sandboxd16n2acr1775255475
ACR Login Server: sandboxd16n2acr1775255475.azurecr.io

Node Pools:
  - sys: System pool (Standard_D16ads_v7, 1-1 nodes)
  - sbx: Sandbox pool (Standard_D16ads_v7, 1-50 nodes)

Next Steps:
1. Build and push the orchestrator image:
   export ACR_NAME=sandboxd16n2acr1775255475
   ./scripts/build_push.sh

2. Deploy the orchestrator to Kubernetes:
   ./scripts/deploy_k8s.sh

3. Run smoke tests:
   ./scripts/smoke_test.sh

To delete all resources:
   az group delete --name sandboxd16n2-orchestrator-rg --yes --no-wait
============================================

[DRY RUN] Would save configuration to .azure-config
