import torch
import torch.nn as nn
from transformers import PreTrainedModel, DistilBertModel
from transformers.modeling_outputs import SequenceClassifierOutput
from .configuration_fourthwall import FourthWallConfig

LABEL_COLS = [
    '000 - Normal',
    '272 - Protocol Manipulation',
    '242 - Code Injection',
    '88 - OS Command Injection',
    '126 - Path Traversal',
    '66 - SQL Injection',
    '16 - Dictionary-based Password Attack',
    '310 - Scanning for Vulnerable Software',
    '153 - Input Data Manipulation',
    '274 - HTTP Verb Tampering',
    '194 - Fake the Source of Data',
    '34 - HTTP Response Splitting',
    '33 - HTTP Request Smuggling'
]

BLOCK_CLASSES = {
    '242 - Code Injection', '88 - OS Command Injection', '126 - Path Traversal',
    '66 - SQL Injection', '16 - Dictionary-based Password Attack',
    '153 - Input Data Manipulation', '274 - HTTP Verb Tampering',
    '194 - Fake the Source of Data', '34 - HTTP Response Splitting',
    '33 - HTTP Request Smuggling'
}

DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

class FourthWallForSequenceClassification(PreTrainedModel):
    config_class = FourthWallConfig
    base_model_prefix = "bert"

    def __init__(self, config):
        super().__init__(config)
        self.num_labels = config.num_labels
        self.config = config
        
        # In our checkpoint, the backbone is self.bert
        self.bert = DistilBertModel(config)
        self.classifier = nn.Sequential(
            nn.Dropout(config.seq_classif_dropout),
            nn.Linear(config.dim, config.num_labels)
        )
        self.post_init()

    def forward(
        self,
        input_ids=None,
        attention_mask=None,
        head_mask=None,
        inputs_embeds=None,
        labels=None,
        output_attentions=None,
        output_hidden_states=None,
        return_dict=None,
    ):
        return_dict = return_dict if return_dict is not None else self.config.use_return_dict

        outputs = self.bert(
            input_ids=input_ids,
            attention_mask=attention_mask,
            head_mask=head_mask,
            inputs_embeds=inputs_embeds,
            output_attentions=output_attentions,
            output_hidden_states=output_hidden_states,
            return_dict=return_dict,
        )

        hidden_state = outputs[0]  # (bs, seq_len, dim)
        cls_output = hidden_state[:, 0]  # (bs, dim)
        logits = self.classifier(cls_output)  # (bs, num_labels)

        loss = None
        if labels is not None:
            loss_fct = nn.BCEWithLogitsLoss()
            loss = loss_fct(logits, labels)

        if not return_dict:
            output = (logits,) + outputs[1:]
            return ((loss,) + output) if loss is not None else output

        return SequenceClassifierOutput(
            loss=loss,
            logits=logits,
            hidden_states=outputs.hidden_states if output_hidden_states else None,
            attentions=outputs.attentions if output_attentions else None,
        )

    @classmethod
    def serialize_request(cls, method="GET", path="/", ua="", ct="", body="", max_body_chars=500):
        """Serialize raw HTTP components into model input format."""
        parts = []
        m = str(method).strip().upper() if method and str(method).lower() not in ('nan', 'none', '') else "GET"
        parts.append(f"[METHOD] {m}")
        
        p = str(path).strip() if path and str(path).lower() not in ('nan', 'none', '') else "/"
        if not p.startswith("/"):
            p = "/" + p
        parts.append(f"[PATH] {p}")
        
        ua_str = str(ua).strip() if ua and str(ua).lower() not in ('nan', 'none', '') else DEFAULT_UA
        parts.append(f"[UA] {ua_str}")
        
        if ct and str(ct).lower() not in ('nan', 'none', ''):
            parts.append(f"[CT] {str(ct).strip()}")
            
        if body and str(body).lower() not in ('nan', 'none', ''):
            b_str = str(body).strip()[:max_body_chars]
            parts.append(f"[BODY] {b_str}")
            
        return " ".join(parts)
