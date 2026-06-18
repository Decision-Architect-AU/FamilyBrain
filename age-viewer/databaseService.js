"use strict";

var _interopRequireDefault = require("@babel/runtime/helpers/interopRequireDefault");

var _typeof = require("@babel/runtime/helpers/typeof");

var _regenerator = _interopRequireDefault(require("@babel/runtime/regenerator"));

var _asyncToGenerator2 = _interopRequireDefault(require("@babel/runtime/helpers/asyncToGenerator"));

var _classCallCheck2 = _interopRequireDefault(require("@babel/runtime/helpers/classCallCheck"));

var _createClass2 = _interopRequireDefault(require("@babel/runtime/helpers/createClass"));

var _SQLFlavorManager = require("../tools/SQLFlavorManager");

var util = _interopRequireWildcard(require("util"));

function _getRequireWildcardCache(nodeInterop) { if (typeof WeakMap !== "function") return null; var cacheBabelInterop = new WeakMap(); var cacheNodeInterop = new WeakMap(); return (_getRequireWildcardCache = function _getRequireWildcardCache(nodeInterop) { return nodeInterop ? cacheNodeInterop : cacheBabelInterop; })(nodeInterop); }

function _interopRequireWildcard(obj, nodeInterop) { if (!nodeInterop && obj && obj.__esModule) { return obj; } if (obj === null || _typeof(obj) !== "object" && typeof obj !== "function") { return { "default": obj }; } var cache = _getRequireWildcardCache(nodeInterop); if (cache && cache.has(obj)) { return cache.get(obj); } var newObj = {}; var hasPropertyDescriptor = Object.defineProperty && Object.getOwnPropertyDescriptor; for (var key in obj) { if (key !== "default" && Object.prototype.hasOwnProperty.call(obj, key)) { var desc = hasPropertyDescriptor ? Object.getOwnPropertyDescriptor(obj, key) : null; if (desc && (desc.get || desc.set)) { Object.defineProperty(newObj, key, desc); } else { newObj[key] = obj[key]; } } } newObj["default"] = obj; if (cache) { cache.set(obj, newObj); } return newObj; }

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
var AgensGraphRepository = require('../models/agensgraph/agensGraphRepository');

var DatabaseService = /*#__PURE__*/function () {
  function DatabaseService() {
    (0, _classCallCheck2["default"])(this, DatabaseService);
    this._agensDatabaseHelper = null;
  }

  (0, _createClass2["default"])(DatabaseService, [{
    key: "getMetaData",
    value: function () {
      var _getMetaData = (0, _asyncToGenerator2["default"])( /*#__PURE__*/_regenerator["default"].mark(function _callee() {
        var metadata, connectionInfo;
        return _regenerator["default"].wrap(function _callee$(_context) {
          while (1) {
            switch (_context.prev = _context.next) {
              case 0:
                metadata = {};
                _context.prev = 1;
                connectionInfo = this.getConnectionInfo();
                _context.next = 5;
                return this.getNodes();

              case 5:
                metadata.nodes = _context.sent;
                _context.next = 8;
                return this.getEdges();

              case 8:
                metadata.edges = _context.sent;
                _context.next = 11;
                return this.getPropertyKeys();

              case 11:
                metadata.propertyKeys = _context.sent;
                metadata.graph = connectionInfo.graph;
                metadata.database = connectionInfo.database;
                _context.next = 16;
                return this.getRole();

              case 16:
                metadata.role = _context.sent;
                _context.next = 22;
                break;

              case 19:
                _context.prev = 19;
                _context.t0 = _context["catch"](1);
                throw _context.t0;

              case 22:
                return _context.abrupt("return", metadata);

              case 23:
              case "end":
                return _context.stop();
            }
          }
        }, _callee, this, [[1, 19]]);
      }));

      function getMetaData() {
        return _getMetaData.apply(this, arguments);
      }

      return getMetaData;
    }()
  }, {
    key: "getGraphLabels",
    value: function () {
      var _getGraphLabels = (0, _asyncToGenerator2["default"])( /*#__PURE__*/_regenerator["default"].mark(function _callee2() {
        var agensDatabaseHelper, queryResult;
        return _regenerator["default"].wrap(function _callee2$(_context2) {
          while (1) {
            switch (_context2.prev = _context2.next) {
              case 0:
                agensDatabaseHelper = this._agensDatabaseHelper;
                queryResult = {};
                _context2.prev = 2;
                _context2.next = 5;
                return agensDatabaseHelper.execute((0, _SQLFlavorManager.getQuery)(agensDatabaseHelper.flavor, 'graph_labels'), [this.getConnectionInfo().graph]);

              case 5:
                queryResult = _context2.sent;
                _context2.next = 11;
                break;

              case 8:
                _context2.prev = 8;
                _context2.t0 = _context2["catch"](2);
                throw _context2.t0;

              case 11:
                return _context2.abrupt("return", queryResult.rows);

              case 12:
              case "end":
                return _context2.stop();
            }
          }
        }, _callee2, this, [[2, 8]]);
      }));

      function getGraphLabels() {
        return _getGraphLabels.apply(this, arguments);
      }

      return getGraphLabels;
    }()
  }, {
    key: "getGraphLabelCount",
    value: function () {
      var _getGraphLabelCount = (0, _asyncToGenerator2["default"])( /*#__PURE__*/_regenerator["default"].mark(function _callee3(labelName, labelKind) {
        var agensDatabaseHelper, query, queryResult;
        return _regenerator["default"].wrap(function _callee3$(_context3) {
          while (1) {
            switch (_context3.prev = _context3.next) {
              case 0:
                agensDatabaseHelper = this._agensDatabaseHelper;
                query = null;

                if (labelKind === 'v') {
                  query = util.format((0, _SQLFlavorManager.getQuery)(agensDatabaseHelper.flavor, 'label_count_vertex'), "".concat(this.getConnectionInfo().graph, ".").concat(labelName));
                } else if (labelKind === 'e') {
                  query = util.format((0, _SQLFlavorManager.getQuery)(agensDatabaseHelper.flavor, 'label_count_edge'), "".concat(this.getConnectionInfo().graph, ".").concat(labelName));
                }

                _context3.next = 5;
                return agensDatabaseHelper.execute(query);

              case 5:
                queryResult = _context3.sent;
                return _context3.abrupt("return", queryResult.rows);

              case 7:
              case "end":
                return _context3.stop();
            }
          }
        }, _callee3, this);
      }));

      function getGraphLabelCount(_x, _x2) {
        return _getGraphLabelCount.apply(this, arguments);
      }

      return getGraphLabelCount;
    }()
  }, {
    key: "getNodes",
    value: function () {
      var _getNodes = (0, _asyncToGenerator2["default"])( /*#__PURE__*/_regenerator["default"].mark(function _callee4() {
        var agensDatabaseHelper, queryResult;
        return _regenerator["default"].wrap(function _callee4$(_context4) {
          while (1) {
            switch (_context4.prev = _context4.next) {
              case 0:
                agensDatabaseHelper = this._agensDatabaseHelper;
                _context4.next = 3;
                return agensDatabaseHelper.execute(util.format((0, _SQLFlavorManager.getQuery)(agensDatabaseHelper.flavor, 'meta_nodes'), agensDatabaseHelper._graph, agensDatabaseHelper._graph));

              case 3:
                queryResult = _context4.sent;
                return _context4.abrupt("return", queryResult.rows);

              case 5:
              case "end":
                return _context4.stop();
            }
          }
        }, _callee4, this);
      }));

      function getNodes() {
        return _getNodes.apply(this, arguments);
      }

      return getNodes;
    }()
  }, {
    key: "getEdges",
    value: function () {
      var _getEdges = (0, _asyncToGenerator2["default"])( /*#__PURE__*/_regenerator["default"].mark(function _callee5() {
        var agensDatabaseHelper, queryResult;
        return _regenerator["default"].wrap(function _callee5$(_context5) {
          while (1) {
            switch (_context5.prev = _context5.next) {
              case 0:
                agensDatabaseHelper = this._agensDatabaseHelper;
                _context5.next = 3;
                return agensDatabaseHelper.execute(util.format((0, _SQLFlavorManager.getQuery)(agensDatabaseHelper.flavor, 'meta_edges'), agensDatabaseHelper._graph, agensDatabaseHelper._graph));

              case 3:
                queryResult = _context5.sent;
                return _context5.abrupt("return", queryResult.rows);

              case 5:
              case "end":
                return _context5.stop();
            }
          }
        }, _callee5, this);
      }));

      function getEdges() {
        return _getEdges.apply(this, arguments);
      }

      return getEdges;
    }()
  }, {
    key: "getPropertyKeys",
    value: function () {
      var _getPropertyKeys = (0, _asyncToGenerator2["default"])( /*#__PURE__*/_regenerator["default"].mark(function _callee6() {
        var agensDatabaseHelper, queryResult;
        return _regenerator["default"].wrap(function _callee6$(_context6) {
          while (1) {
            switch (_context6.prev = _context6.next) {
              case 0:
                agensDatabaseHelper = this._agensDatabaseHelper;
                _context6.next = 3;
                return agensDatabaseHelper.execute((0, _SQLFlavorManager.getQuery)(agensDatabaseHelper.flavor, 'property_keys'));

              case 3:
                queryResult = _context6.sent;
                return _context6.abrupt("return", queryResult.rows);

              case 5:
              case "end":
                return _context6.stop();
            }
          }
        }, _callee6, this);
      }));

      function getPropertyKeys() {
        return _getPropertyKeys.apply(this, arguments);
      }

      return getPropertyKeys;
    }()
  }, {
    key: "getRole",
    value: function () {
      var _getRole = (0, _asyncToGenerator2["default"])( /*#__PURE__*/_regenerator["default"].mark(function _callee7() {
        var agensDatabaseHelper, queryResult;
        return _regenerator["default"].wrap(function _callee7$(_context7) {
          while (1) {
            switch (_context7.prev = _context7.next) {
              case 0:
                agensDatabaseHelper = this._agensDatabaseHelper;
                _context7.next = 3;
                return agensDatabaseHelper.execute((0, _SQLFlavorManager.getQuery)(agensDatabaseHelper.flavor, 'get_role'), [this.getConnectionInfo().user]);

              case 3:
                queryResult = _context7.sent;
                return _context7.abrupt("return", queryResult.rows[0]);

              case 5:
              case "end":
                return _context7.stop();
            }
          }
        }, _callee7, this);
      }));

      function getRole() {
        return _getRole.apply(this, arguments);
      }

      return getRole;
    }()
  }, {
    key: "connectDatabase",
    value: function () {
      var _connectDatabase = (0, _asyncToGenerator2["default"])( /*#__PURE__*/_regenerator["default"].mark(function _callee8(connectionInfo) {
        var agensDatabaseHelper, client;
        return _regenerator["default"].wrap(function _callee8$(_context8) {
          while (1) {
            switch (_context8.prev = _context8.next) {
              case 0:
                agensDatabaseHelper = this._agensDatabaseHelper;

                if (agensDatabaseHelper == null) {
                  this._agensDatabaseHelper = new AgensGraphRepository(connectionInfo);
                  agensDatabaseHelper = this._agensDatabaseHelper;
                }

                _context8.prev = 2;
                _context8.next = 5;
                return agensDatabaseHelper.getConnection(agensDatabaseHelper.getConnectionInfo(), true);

              case 5:
                client = _context8.sent;
                client.release();
                _context8.next = 13;
                break;

              case 9:
                _context8.prev = 9;
                _context8.t0 = _context8["catch"](2);
                this._agensDatabaseHelper = null;
                throw _context8.t0;

              case 13:
                return _context8.abrupt("return", true);

              case 14:
              case "end":
                return _context8.stop();
            }
          }
        }, _callee8, this, [[2, 9]]);
      }));

      function connectDatabase(_x3) {
        return _connectDatabase.apply(this, arguments);
      }

      return connectDatabase;
    }()
  }, {
    key: "disconnectDatabase",
    value: function () {
      var _disconnectDatabase = (0, _asyncToGenerator2["default"])( /*#__PURE__*/_regenerator["default"].mark(function _callee9() {
        var agensDatabaseHelper, isRelease;
        return _regenerator["default"].wrap(function _callee9$(_context9) {
          while (1) {
            switch (_context9.prev = _context9.next) {
              case 0:
                agensDatabaseHelper = this._agensDatabaseHelper;

                if (!(agensDatabaseHelper == null)) {
                  _context9.next = 6;
                  break;
                }

                console.log('Already Disconnected');
                return _context9.abrupt("return", false);

              case 6:
                _context9.next = 8;
                return this._agensDatabaseHelper.releaseConnection();

              case 8:
                isRelease = _context9.sent;

                if (!isRelease) {
                  _context9.next = 14;
                  break;
                }

                this._agensDatabaseHelper = null;
                return _context9.abrupt("return", true);

              case 14:
                console.log('Failed releaseConnection()');
                return _context9.abrupt("return", false);

              case 16:
              case "end":
                return _context9.stop();
            }
          }
        }, _callee9, this);
      }));

      function disconnectDatabase() {
        return _disconnectDatabase.apply(this, arguments);
      }

      return disconnectDatabase;
    }()
  }, {
    key: "getConnectionStatus",
    value: function () {
      var _getConnectionStatus = (0, _asyncToGenerator2["default"])( /*#__PURE__*/_regenerator["default"].mark(function _callee10() {
        var agensDatabaseHelper, client;
        return _regenerator["default"].wrap(function _callee10$(_context10) {
          while (1) {
            switch (_context10.prev = _context10.next) {
              case 0:
                agensDatabaseHelper = this._agensDatabaseHelper;

                if (!(agensDatabaseHelper == null)) {
                  _context10.next = 3;
                  break;
                }

                return _context10.abrupt("return", false);

              case 3:
                _context10.prev = 3;
                _context10.next = 6;
                return AgensGraphRepository.getConnection(agensDatabaseHelper.getConnectionInfo());

              case 6:
                client = _context10.sent;
                client.release();
                _context10.next = 13;
                break;

              case 10:
                _context10.prev = 10;
                _context10.t0 = _context10["catch"](3);
                return _context10.abrupt("return", false);

              case 13:
                return _context10.abrupt("return", true);

              case 14:
              case "end":
                return _context10.stop();
            }
          }
        }, _callee10, this, [[3, 10]]);
      }));

      function getConnectionStatus() {
        return _getConnectionStatus.apply(this, arguments);
      }

      return getConnectionStatus;
    }()
  }, {
    key: "getConnectionInfo",
    value: function getConnectionInfo() {
      if (this.isConnected() === false) throw new Error("Not connected");
      return this._agensDatabaseHelper.getConnectionInfo();
    }
  }, {
    key: "isConnected",
    value: function isConnected() {
      return this._agensDatabaseHelper != null;
    }
  }, {
    key: "agensDatabaseHelper",
    get: function get() {
      return this._agensDatabaseHelper;
    }
  }, {
    key: "convertEdge",
    value: function convertEdge(_ref) {
      var label = _ref.label,
          id = _ref.id,
          start = _ref.start,
          end = _ref.end,
          props = _ref.props;
      return {
        label: label,
        id: "".concat(id.oid, ".").concat(id.id),
        start: "".concat(start.oid, ".").concat(start.id),
        end: "".concat(end.oid, ".").concat(end.id),
        properties: props
      };
    }
  }]);
  return DatabaseService;
}();

module.exports = DatabaseService;
//# sourceMappingURL=databaseService.js.map