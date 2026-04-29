Notes
29/4 please revise the model by only focus on UK ev
Let me read the SD model to understand how quantities flow before restructuring:

Read c:\Users\3054109\OneDrive - Queen's University Belfast\Model building\ev-supply-chain\model\sd_model.py (lines 2-101)
Read c:\Users\3054109\OneDrive - Queen's University Belfast\Model building\ev-supply-chain\model\sd_model.py (lines 101-220)
Now I have a complete picture of the model. This is a significant restructuring — I'll rescale the OEM and cell layers to UK, keep minerals global (price-taker), and add UK-specific parameters (ZEV mandate, Brexit Rules of Origin). Let me implement this systematically.

Read c:\Users\3054109\OneDrive - Queen's University Belfast\Model building\ev-supply-chain\model\sd_model.py (lines 219-298)