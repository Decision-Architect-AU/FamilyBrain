"use strict";

var _interopRequireDefault = require("@babel/runtime/helpers/interopRequireDefault");

var _regenerator = _interopRequireDefault(require("@babel/runtime/regenerator"));

var _asyncToGenerator2 = _interopRequireDefault(require("@babel/runtime/helpers/asyncToGenerator"));

var _classCallCheck2 = _interopRequireDefault(require("@babel/runtime/helpers/classCallCheck"));

var _createClass2 = _interopRequireDefault(require("@babel/runtime/helpers/createClass"));

function _createForOfIteratorHelper(o, allowArrayLike) { var it = typeof Symbol !== "undefined" && o[Symbol.iterator] || o["@@iterator"]; if (!it) { if (Array.isArray(o) || (it = _unsupportedIterableToArray(o)) || allowArrayLike && o && typeof o.length === "number") { if (it) o = it; var i = 0; var F = function F() {}; return { s: F, n: function n() { if (i >= o.length) return { done: true }; return { done: false, value: o[i++] }; }, e: function e(_e) { throw _e; }, f: F }; } throw new TypeError("Invalid attempt to iterate non-iterable instance.\nIn order to be iterable, non-array objects must have a [Symbol.iterator]() method."); } var normalCompletion = true, didErr = false, err; return { s: function s() { it = it.call(o); }, n: function n() { var step = it.next(); normalCompletion = step.done; return step; }, e: function e(_e2) { didErr = true; err = _e2; }, f: function f() { try { if (!normalCompletion && it["return"] != null) it["return"](); } finally { if (didErr) throw err; } } }; }

function _unsupportedIterableToArray(o, minLen) { if (!o) return; if (typeof o === "string") return _arrayLikeToArray(o, minLen); var n = Object.prototype.toString.call(o).slice(8, -1); if (n === "Object" && o.constructor) n = o.constructor.name; if (n === "Map" || n === "Set") return Array.from(o); if (n === "Arguments" || /^(?:Ui|I)nt(?:8|16|32)(?:Clamped)?Array$/.test(n)) return _arrayLikeToArray(o, minLen); }

function _arrayLikeToArray(arr, len) { if (len == null || len > arr.length) len = arr.length; for (var i = 0, arr2 = new Array(len); i < len; i++) { arr2[i] = arr[i]; } return arr2; }

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
var sessionService = require('../services/sessionService');

var winston = require('winston');

var DatabseController = /*#__PURE__*/function () {
  function DatabseController() {
    (0, _classCallCheck2["default"])(this, DatabseController);
  }

  (0, _createClass2["default"])(DatabseController, [{
    key: "connectDatabase",
    value: function () {
      var _connectDatabase = (0, _asyncToGenerator2["default"])( /*#__PURE__*/_regenerator["default"].mark(function _callee(req, res, next) {
        var databaseService, connectionInfo;
        return _regenerator["default"].wrap(function _callee$(_context) {
          while (1) {
            switch (_context.prev = _context.next) {
              case 0:
                databaseService = sessionService.get(req.sessionID);

                if (databaseService.isConnected()) {
                  _context.next = 4;
                  break;
                }

                _context.next = 4;
                return databaseService.connectDatabase(req.body);

              case 4:
                connectionInfo = databaseService.getConnectionInfo();
                res.status(200).json(connectionInfo).end();

              case 6:
              case "end":
                return _context.stop();
            }
          }
        }, _callee);
      }));

      function connectDatabase(_x, _x2, _x3) {
        return _connectDatabase.apply(this, arguments);
      }

      return connectDatabase;
    }()
  }, {
    key: "disconnectDatabase",
    value: function () {
      var _disconnectDatabase = (0, _asyncToGenerator2["default"])( /*#__PURE__*/_regenerator["default"].mark(function _callee2(req, res, next) {
        var databaseService, isDisconnect;
        return _regenerator["default"].wrap(function _callee2$(_context2) {
          while (1) {
            switch (_context2.prev = _context2.next) {
              case 0:
                databaseService = sessionService.get(req.sessionID);

                if (!databaseService.isConnected()) {
                  _context2.next = 8;
                  break;
                }

                _context2.next = 4;
                return databaseService.disconnectDatabase();

              case 4:
                isDisconnect = _context2.sent;

                if (isDisconnect) {
                  res.status(200).json({
                    msg: 'Disconnect Successful'
                  }).end();
                } else {
                  res.status(500).json({
                    msg: 'Already Disconnected'
                  }).end();
                }

                _context2.next = 9;
                break;

              case 8:
                throw new Error('Not connected');

              case 9:
              case "end":
                return _context2.stop();
            }
          }
        }, _callee2);
      }));

      function disconnectDatabase(_x4, _x5, _x6) {
        return _disconnectDatabase.apply(this, arguments);
      }

      return disconnectDatabase;
    }()
  }, {
    key: "getStatus",
    value: function () {
      var _getStatus = (0, _asyncToGenerator2["default"])( /*#__PURE__*/_regenerator["default"].mark(function _callee3(req, res, next) {
        var databaseService;
        return _regenerator["default"].wrap(function _callee3$(_context3) {
          while (1) {
            switch (_context3.prev = _context3.next) {
              case 0:
                databaseService = sessionService.get(req.sessionID);

                if (!databaseService.isConnected()) {
                  _context3.next = 7;
                  break;
                }

                _context3.next = 4;
                return databaseService.getConnectionStatus();

              case 4:
                res.status(200).json(databaseService.getConnectionInfo()).end();
                _context3.next = 8;
                break;

              case 7:
                throw new Error('Not connected');

              case 8:
              case "end":
                return _context3.stop();
            }
          }
        }, _callee3);
      }));

      function getStatus(_x7, _x8, _x9) {
        return _getStatus.apply(this, arguments);
      }

      return getStatus;
    }()
  }, {
    key: "getMetadata",
    value: function () {
      var _getMetadata = (0, _asyncToGenerator2["default"])( /*#__PURE__*/_regenerator["default"].mark(function _callee4(req, res, next) {
        var databaseService, metadata;
        return _regenerator["default"].wrap(function _callee4$(_context4) {
          while (1) {
            switch (_context4.prev = _context4.next) {
              case 0:
                databaseService = sessionService.get(req.sessionID);

                if (!databaseService.isConnected()) {
                  _context4.next = 8;
                  break;
                }

                _context4.next = 4;
                return databaseService.getMetaData();

              case 4:
                metadata = _context4.sent;
                res.status(200).json(metadata).end();
                _context4.next = 9;
                break;

              case 8:
                throw new Error('Not connected');

              case 9:
              case "end":
                return _context4.stop();
            }
          }
        }, _callee4);
      }));

      function getMetadata(_x10, _x11, _x12) {
        return _getMetadata.apply(this, arguments);
      }

      return getMetadata;
    }()
  }, {
    key: "getMetaChart",
    value: function () {
      var _getMetaChart = (0, _asyncToGenerator2["default"])( /*#__PURE__*/_regenerator["default"].mark(function _callee5(req, res, next) {
        var databaseService, metadata, graphLabels, _iterator, _step, labels, countResults, idx;

        return _regenerator["default"].wrap(function _callee5$(_context5) {
          while (1) {
            switch (_context5.prev = _context5.next) {
              case 0:
                databaseService = sessionService.get(req.sessionID);

                if (!databaseService.isConnected()) {
                  _context5.next = 34;
                  break;
                }

                metadata = [];
                _context5.prev = 3;
                _context5.next = 6;
                return databaseService.getGraphLabels();

              case 6:
                graphLabels = _context5.sent;
                _iterator = _createForOfIteratorHelper(graphLabels);
                _context5.prev = 8;

                _iterator.s();

              case 10:
                if ((_step = _iterator.n()).done) {
                  _context5.next = 18;
                  break;
                }

                labels = _step.value;
                _context5.next = 14;
                return databaseService.getGraphLabelCount(labels.la_name, labels.la_kind);

              case 14:
                countResults = _context5.sent;

                for (idx in countResults) {
                  if (idx > 0) {
                    labels.la_name = labels.la_name + "-" + idx;
                    labels.la_oid = labels.la_oid + idx * 0.1;
                  }

                  metadata.push(Object.assign({}, labels, countResults[idx]));
                }

              case 16:
                _context5.next = 10;
                break;

              case 18:
                _context5.next = 23;
                break;

              case 20:
                _context5.prev = 20;
                _context5.t0 = _context5["catch"](8);

                _iterator.e(_context5.t0);

              case 23:
                _context5.prev = 23;

                _iterator.f();

                return _context5.finish(23);

              case 26:
                res.status(200).json(metadata).end();
                _context5.next = 32;
                break;

              case 29:
                _context5.prev = 29;
                _context5.t1 = _context5["catch"](3);
                res.status(500).json(metadata).end();

              case 32:
                _context5.next = 35;
                break;

              case 34:
                throw new Error('Not connected');

              case 35:
              case "end":
                return _context5.stop();
            }
          }
        }, _callee5, null, [[3, 29], [8, 20, 23, 26]]);
      }));

      function getMetaChart(_x13, _x14, _x15) {
        return _getMetaChart.apply(this, arguments);
      }

      return getMetaChart;
    }()
  }]);
  return DatabseController;
}();

module.exports = DatabseController;
//# sourceMappingURL=databaseController.js.map