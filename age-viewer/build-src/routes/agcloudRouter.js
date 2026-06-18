"use strict";

/*
 * Copyright 2020 Bitnine Co., Ltd.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
var express = require("express");

var AgcloudController = require("../controllers/agcloudController");

var router = express.Router();
var agcloudController = new AgcloudController();

var _require = require('../common/Routes'),
    wrap = _require.wrap; // Execute Cypher Query


router.post("/", wrap(agcloudController.connectDatabase));
module.exports = router;
//# sourceMappingURL=agcloudRouter.js.map