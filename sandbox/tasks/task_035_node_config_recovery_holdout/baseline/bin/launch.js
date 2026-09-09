const fs = require("node:fs");
const path = require("node:path");

function loadService(file) {
  const value = JSON.parse(fs.readFileSync(file, "utf8"));
  return { service: value.service, port: value.port, retries: value.retries };
}

module.exports = { loadService };
