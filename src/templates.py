import os
import json

class TemplateManager:
    def __init__(self, data_dir: str):
        self.config_path = os.path.join(data_dir, "persona.json")
        self.default_config = {
            "bot_name": "Intan",
            "origin": "Bandung",
            "age": "20 thn",
            "vcs_price": "100K",
            "vip_price": "50K",
            "desah_price": "+25K",
            "squirt_price": "+50K",
            "dildo_price": "+25K"
        }
        self.load_config()

    def load_config(self):
        """Load configuration from persona.json."""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    self.config = json.load(f)
            except Exception as e:
                print(f"[!] Error loading persona.json: {e}")
                self.config = dict(self.default_config)
        else:
            self.config = dict(self.default_config)
            self.save_config()

    def save_config(self):
        """Save configuration to persona.json."""
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=2, ensure_ascii=False)

    def get_pricelist_template(self) -> str:
        """Generate dynamic pricelist based on active persona config."""
        self.load_config()
        name = self.config.get("bot_name", "Intan")
        vcs = self.config.get("vcs_price", "100K")
        vip = self.config.get("vip_price", "50K")
        desah = self.config.get("desah_price", "+25K")
        squirt = self.config.get("squirt_price", "+50K")
        dildo = self.config.get("dildo_price", "+25K")

        return (
            f"𝑷𝒓𝒊𝒄𝒆𝒍𝒊𝒔𝒕 {name}\n\n"
            f"VCS — cuma {vcs}\n"
            f"Murah banget, tanpa durasi, sudah Full Face & Full Body, hanya 1x crot saja\n\n"
            f"Request Tambahan:\n"
            f"• Desah: {desah}\n"
            f"• Squirt: {squirt}\n"
            f"• Dildo: {dildo}\n\n\n"
            f"VIP Group — cuma {vip}\n"
            f"Bayar sekali aja, akses permanen."
        )

    def replace_placeholders(self, text: str) -> str:
        """Replace {bot_name}, {vcs_price}, {vip_price}, etc. in text."""
        self.load_config()
        for key, val in self.config.items():
            text = text.replace(f"{{{key}}}", str(val))
        return text
