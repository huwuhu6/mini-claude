const fs = require("node:fs");

function loadService(file) {
  const value = JSON.parse(fs.readFileSync(file, "utf8"));
  return { service: value.service, port: Number(value.port), retries: Number(value.retries) };
}

module.exports = { loadService };
