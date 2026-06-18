"use strict";

var _interopRequireDefault = require("@babel/runtime/helpers/interopRequireDefault");

var _regenerator = _interopRequireDefault(require("@babel/runtime/regenerator"));

var _asyncToGenerator2 = _interopRequireDefault(require("@babel/runtime/helpers/asyncToGenerator"));

var _classCallCheck2 = _interopRequireDefault(require("@babel/runtime/helpers/classCallCheck"));

var _createClass2 = _interopRequireDefault(require("@babel/runtime/helpers/createClass"));

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
var CypherService = require("../services/cypherService");

var sessionService = require("../services/sessionService");

var CypherController = /*#__PURE__*/function () {
  function CypherController() {
    (0, _classCallCheck2["default"])(this, CypherController);
  }

  (0, _createClass2["default"])(CypherController, [{
    key: "executeCypher",
    value: function () {
      var _executeCypher = (0, _asyncToGenerator2["default"])( /*#__PURE__*/_regenerator["default"].mark(function _callee(req, res) {
        var connectorService, cypherService, data;
        return _regenerator["default"].wrap(function _callee$(_context) {
          while (1) {
            switch (_context.prev = _context.next) {
              case 0:
                connectorService = sessionService.get(req.sessionID);

                if (!connectorService.isConnected()) {
                  _context.next = 9;
                  break;
                }

                cypherService = new CypherService(connectorService.agensDatabaseHelper);
                _context.next = 5;
                return cypherService.executeCypher(req.body.cmd);

              case 5:
                data = _context.sent;
                res.status(200).json(data).end();
                _context.next = 10;
                break;

              case 9:
                throw new Error("Not connected");

              case 10:
              case "end":
                return _context.stop();
            }
          }
        }, _callee);
      }));

      function executeCypher(_x, _x2) {
        return _executeCypher.apply(this, arguments);
      }

      return executeCypher;
    }()
  }]);
  return CypherController;
}();

module.exports = CypherController;
//# sourceMappingURL=cypherController.js.map