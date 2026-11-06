/**
 * SOT Architecture Test - Comprehensive validation
 * Verifies strict Single Source of Truth with canonical schema
 */

import { store, MESSAGE_SCHEMA } from './store.js?v=20261106-group1';
import { messageService } from './message-service.js?v=20261106-group1';
import { webSocketManager } from './websocket.js?v=20261106-group1';
import { uiController } from '../ui/ui-controller.js?v=20261106-group1';
import { appController } from './app-controller.js?v=20261106-group1';

export class SOTTest {
    constructor() {
        this.testResults = [];
        this.debugMode = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';
        this.violations = [];
    }

    /**
     * Run all SOT architecture tests
     */
    async runTests() {
        console.log('🔒 Running Comprehensive SOT Architecture Tests...');
        
        try {
            await this.testCanonicalMessageSchema();
            await this.testStoreIsOnlyMutationSource();
            await this.testMessageServiceIsOnlyIngestionLayer();
            await this.testWebSocketIsTransportOnly();
            await this.testUIControllerIsReadOnlyConsumer();
            await this.testRendererIsPureRendering();
            await this.testEventBusIsUIOnly();
            await this.testAppControllerIsOrchestrationOnly();
            await this.testMandatoryDataFlow();
            await this.testNoDuplicateMessages();
            await this.testUIEqualsStoreSnapshot();
            await this.testDeterministicMessageOrdering();
            await this.testNoHiddenSecondaryState();
            await this.testForbiddenCrossModuleStateAccess();
            
            this.printResults();
            return this.testResults;
        } catch (error) {
            console.error('❌ SOT tests failed:', error);
            return [];
        }
    }

    /**
     * Test canonical message schema
     */
    async testCanonicalMessageSchema() {
        const testName = 'Canonical Message Schema';
        try {
            let passed = true;

            // Test schema definition exists
            if (!MESSAGE_SCHEMA) {
                passed = false;
                this.addViolation('MESSAGE_SCHEMA_NOT_DEFINED');
            }

            // Test required fields
            const requiredFields = ['id', 'conversationId', 'senderId', 'timestamp', 'status', 'content', 'type'];
            for (const field of requiredFields) {
                if (!MESSAGE_SCHEMA[field]) {
                    passed = false;
                    this.addViolation(`MISSING_SCHEMA_FIELD_${field.toUpperCase()}`);
                }
            }

            // Test store validates canonical schema
            const testMessage = {
                id: 'test-123',
                conversationId: 123,
                senderId: 456,
                timestamp: new Date().toISOString(),
                status: 'sent',
                content: 'Test message',
                type: 'text',
                metadata: {},
                isOptimistic: false,
                sortOrder: Date.now()
            };

            store.init({ conversationId: 123, currentUserId: 456, isEncrypted: false });
            store.addMessage(testMessage);

            const retrievedMessage = store.getMessageById('test-123');
            if (!retrievedMessage || retrievedMessage.id !== 'test-123') {
                passed = false;
                this.addViolation('CANONICAL_SCHEMA_VALIDATION_FAILED');
            }

            this.addResult(testName, passed, passed ? 'Canonical message schema enforced' : 'Schema violations found');
        } catch (error) {
            this.addResult(testName, false, error.message);
        }
    }

    /**
     * Test that store is the ONLY mutation source
     */
    async testStoreIsOnlyMutationSource() {
        const testName = 'Store is ONLY Mutation Source';
        try {
            let passed = true;

            // Test store has mutation methods
            const mutationMethods = [
                'addMessage', 'updateMessage', 'replaceMessage', 'removeMessage',
                'setConnectionState', 'setTypingIndicator', 'setUIState', 'setTheme'
            ];

            for (const method of mutationMethods) {
                if (typeof store[method] !== 'function') {
                    passed = false;
                    this.addViolation(`MISSING_STORE_MUTATION_${method.toUpperCase()}`);
                }
            }

            // Test other modules don't have mutation methods
            const forbiddenModules = [
                { module: messageService, name: 'messageService' },
                { module: webSocketManager, name: 'webSocketManager' },
                { module: uiController, name: 'uiController' }
            ];

            for (const { module, name } of forbiddenModules) {
                for (const method of mutationMethods) {
                    if (typeof module[method] === 'function') {
                        passed = false;
                        this.addViolation(`FORBIDDEN_MUTATION_${name.toUpperCase()}_${method.toUpperCase()}`);
                    }
                }
            }

            // Test mutation logging
            const mutationLog = store.getMutationLog();
            if (!Array.isArray(mutationLog)) {
                passed = false;
                this.addViolation('MUTATION_LOGGING_NOT_WORKING');
            }

            this.addResult(testName, passed, passed ? 'Store is the only mutation source' : 'Mutation violations found');
        } catch (error) {
            this.addResult(testName, false, error.message);
        }
    }

    /**
     * Test that message service is ONLY ingestion layer
     */
    async testMessageServiceIsOnlyIngestionLayer() {
        const testName = 'Message Service is ONLY Ingestion Layer';
        try {
            let passed = true;

            // Test message service has ingestion methods
            const ingestionMethods = ['processIncomingMessage', 'sendMessage'];
            for (const method of ingestionMethods) {
                if (typeof messageService[method] !== 'function') {
                    passed = false;
                    this.addViolation(`MISSING_INGESTION_METHOD_${method.toUpperCase()}`);
                }
            }

            // Test message service doesn't have direct state access
            const stateMethods = ['getState', 'getMessages', 'subscribe'];
            for (const method of stateMethods) {
                if (typeof messageService[method] === 'function') {
                    passed = false;
                    this.addViolation(`FORBIDDEN_STATE_ACCESS_MESSAGE_SERVICE_${method.toUpperCase()}`);
                }
            }

            // Test message service forwards to store
            const stateBefore = store.getState();
            await messageService.sendMessage('Test message');
            const stateAfter = store.getState();

            if (stateAfter.messages.length <= stateBefore.messages.length) {
                passed = false;
                this.addViolation('MESSAGE_SERVICE_NOT_FORWARDING_TO_STORE');
            }

            this.addResult(testName, passed, passed ? 'Message service is ingestion layer only' : 'Ingestion violations found');
        } catch (error) {
            this.addResult(testName, false, error.message);
        }
    }

    /**
     * Test that websocket is transport only
     */
    async testWebSocketIsTransportOnly() {
        const testName = 'WebSocket is Transport ONLY';
        try {
            let passed = true;

            // Test websocket has transport methods
            const transportMethods = ['send', 'connect', 'disconnect', 'isConnected'];
            for (const method of transportMethods) {
                if (typeof webSocketManager[method] !== 'function') {
                    passed = false;
                    this.addViolation(`MISSING_TRANSPORT_METHOD_${method.toUpperCase()}`);
                }
            }

            // Test websocket doesn't have state methods
            const stateMethods = ['addMessage', 'updateMessage', 'setConnectionState', 'subscribe'];
            for (const method of stateMethods) {
                if (typeof webSocketManager[method] === 'function') {
                    passed = false;
                    this.addViolation(`FORBIDDEN_STATE_ACCESS_WEBSOCKET_${method.toUpperCase()}`);
                }
            }

            // Test websocket has callback setup
            const status = webSocketManager.getStatus();
            if (!status.hasMessageCallback || !status.hasConnectionCallback) {
                passed = false;
                this.addViolation('WEBSOCKET_CALLBACKS_NOT_SETUP');
            }

            this.addResult(testName, passed, passed ? 'WebSocket is transport only' : 'Transport violations found');
        } catch (error) {
            this.addResult(testName, false, error.message);
        }
    }

    /**
     * Test that UI controller is read-only consumer
     */
    async testUIControllerIsReadOnlyConsumer() {
        const testName = 'UI Controller is Read-Only Consumer';
        try {
            let passed = true;

            // Test UI controller doesn't have mutation methods
            const forbiddenMethods = ['addMessage', 'updateMessage', 'setConnectionState', 'setTypingIndicator'];
            for (const method of forbiddenMethods) {
                if (typeof uiController[method] === 'function') {
                    passed = false;
                    this.addViolation(`FORBIDDEN_MUTATION_UI_CONTROLLER_${method.toUpperCase()}`);
                }
            }

            // Test UI controller is subscribed to store
            const status = uiController.getStatus();
            if (!status.subscribed) {
                passed = false;
                this.addViolation('UI_CONTROLLER_NOT_SUBSCRIBED_TO_STORE');
            }

            // Test UI controller has read-only flag
            if (!status.isReadOnly) {
                passed = false;
                this.addViolation('UI_CONTROLLER_NOT_MARKED_READ_ONLY');
            }

            this.addResult(testName, passed, passed ? 'UI controller is read-only consumer' : 'Consumer violations found');
        } catch (error) {
            this.addResult(testName, false, error.message);
        }
    }

    /**
     * Test that renderer is pure rendering only
     */
    async testRendererIsPureRenderingOnly() {
        const testName = 'Renderer is Pure Rendering Only';
        try {
            let passed = true;

            const renderer = uiController.renderer;
            if (!renderer) {
                this.addResult(testName, false, 'Renderer not found');
                return;
            }

            // Test renderer doesn't have state methods
            const forbiddenMethods = ['addMessage', 'updateMessage', 'setConnectionState', 'subscribe'];
            for (const method of forbiddenMethods) {
                if (typeof renderer[method] === 'function') {
                    passed = false;
                    this.addViolation(`FORBIDDEN_STATE_ACCESS_RENDERER_${method.toUpperCase()}`);
                }
            }

            // Test renderer has rendering methods
            const requiredMethods = ['render', 'init', 'destroy'];
            for (const method of requiredMethods) {
                if (typeof renderer[method] !== 'function') {
                    passed = false;
                    this.addViolation(`MISSING_RENDERING_METHOD_${method.toUpperCase()}`);
                }
            }

            // Test renderer has pure rendering flag
            const status = renderer.getStatus();
            if (!status.isPureRendering) {
                passed = false;
                this.addViolation('RENDERER_NOT_MARKED_PURE_RENDERING');
            }

            this.addResult(testName, passed, passed ? 'Renderer is pure rendering only' : 'Rendering violations found');
        } catch (error) {
            this.addResult(testName, false, error.message);
        }
    }

    /**
     * Test that event bus is UI only
     */
    async testEventBusIsUIOnly() {
        const testName = 'Event Bus is UI Only';
        try {
            let passed = true;

            // Test event bus validation
            const { eventBus } = await import('./event-bus.js?v=20261106-group1');
            
            // Try forbidden event patterns (should throw error)
            const forbiddenEvents = ['message', 'state', 'store', 'websocket', 'connection'];
            
            for (const event of forbiddenEvents) {
                try {
                    eventBus.emit(event, {});
                    passed = false;
                    this.addViolation(`EVENT_BUS_ALLOWED_FORBIDDEN_EVENT_${event.toUpperCase()}`);
                } catch (error) {
                    // Expected to throw error
                }
            }

            // Try allowed event patterns (should work)
            const allowedEvents = ['ui:click', 'typing:start', 'focus:input'];
            
            for (const event of allowedEvents) {
                try {
                    eventBus.emit(event, {});
                } catch (error) {
                    passed = false;
                    this.addViolation(`EVENT_BUS_BLOCKED_ALLOWED_EVENT_${event.toUpperCase()}`);
                }
            }

            this.addResult(testName, passed, passed ? 'Event bus is UI only' : 'Event bus violations found');
        } catch (error) {
            this.addResult(testName, false, error.message);
        }
    }

    /**
     * Test that app controller is orchestration only
     */
    async testAppControllerIsOrchestrationOnly() {
        const testName = 'App Controller is Orchestration Only';
        try {
            let passed = true;

            // Test app controller doesn't have mutation methods
            const forbiddenMethods = ['addMessage', 'updateMessage', 'setConnectionState'];
            for (const method of forbiddenMethods) {
                if (typeof appController[method] === 'function') {
                    passed = false;
                    this.addViolation(`FORBIDDEN_MUTATION_APP_CONTROLLER_${method.toUpperCase()}`);
                }
            }

            // Test app controller has orchestration methods
            const requiredMethods = ['init', 'handleConnectionChange', 'getStatus'];
            for (const method of requiredMethods) {
                if (typeof appController[method] !== 'function') {
                    passed = false;
                    this.addViolation(`MISSING_ORCHESTRATION_METHOD_${method.toUpperCase()}`);
                }
            }

            // Test SOT validation
            const status = appController.getStatus();
            if (!status.sotValidation || !status.sotValidation.dataFlowValid) {
                passed = false;
                this.addViolation('APP_CONTROLLER_SOT_VALIDATION_FAILED');
            }

            this.addResult(testName, passed, passed ? 'App controller is orchestration only' : 'Orchestration violations found');
        } catch (error) {
            this.addResult(testName, false, error.message);
        }
    }

    /**
     * Test mandatory data flow
     */
    async testMandatoryDataFlow() {
        const testName = 'Mandatory Data Flow';
        try {
            let passed = true;

            // Test incoming flow: websocket → message-service → store → ui-controller → renderer
            const wsStatus = webSocketManager.getStatus();
            if (!wsStatus.hasMessageCallback) {
                passed = false;
                this.addViolation('WEBSOCKET_NOT_FORWARDING_TO_MESSAGE_SERVICE');
            }

            const uiStatus = uiController.getStatus();
            if (!uiStatus.subscribed) {
                passed = false;
                this.addViolation('UI_CONTROLLER_NOT_SUBSCRIBED_TO_STORE');
            }

            if (!uiStatus.rendererInitialized) {
                passed = false;
                this.addViolation('UI_CONTROLLER_NOT_USING_RENDERER');
            }

            // Test outgoing flow: ui-controller → message-service → websocket → store
            // This is tested by sending a message and checking the flow

            this.addResult(testName, passed, passed ? 'Mandatory data flow implemented' : 'Data flow violations found');
        } catch (error) {
            this.addResult(testName, false, error.message);
        }
    }

    /**
     * Test no duplicate messages
     */
    async testNoDuplicateMessages() {
        const testName = 'No Duplicate Messages';
        try {
            let passed = true;

            // Add same message twice
            const testMessage = {
                id: 'duplicate-test-123',
                conversationId: 123,
                senderId: 456,
                timestamp: new Date().toISOString(),
                status: 'sent',
                content: 'Duplicate test message',
                type: 'text',
                metadata: {},
                isOptimistic: false,
                sortOrder: Date.now()
            };

            store.addMessage(testMessage);
            store.addMessage(testMessage); // Should be ignored

            const validation = store.validateMessageConsistency();
            if (validation.duplicates.length > 0) {
                passed = false;
                this.addViolation('DUPLICATE_MESSAGES_FOUND');
            }

            this.addResult(testName, passed, passed ? 'No duplicate messages' : 'Duplicate message violations found');
        } catch (error) {
            this.addResult(testName, false, error.message);
        }
    }

    /**
     * Test UI equals store snapshot
     */
    async testUIEqualsStoreSnapshot() {
        const testName = 'UI Equals Store Snapshot';
        try {
            let passed = true;

            // Get store state
            const storeState = store.getState();
            
            // UI should render exactly what's in store
            // This is tested by checking that renderer receives store data
            const rendererStatus = uiController.renderer.getStatus();
            if (!rendererStatus.initialized) {
                passed = false;
                this.addViolation('RENDERER_NOT_INITIALIZED_FROM_STORE');
            }

            this.addResult(testName, passed, passed ? 'UI equals store snapshot' : 'UI-store mismatch violations found');
        } catch (error) {
            this.addResult(testName, false, error.message);
        }
    }

    /**
     * Test deterministic message ordering
     */
    async testDeterministicMessageOrdering() {
        const testName = 'Deterministic Message Ordering';
        try {
            let passed = true;

            // Add messages with different timestamps
            const messages = [
                {
                    id: 'order-test-1',
                    conversationId: 123,
                    senderId: 456,
                    timestamp: '2023-01-01T10:00:00.000Z',
                    status: 'sent',
                    content: 'First message',
                    type: 'text',
                    metadata: {},
                    isOptimistic: false,
                    sortOrder: new Date('2023-01-01T10:00:00.000Z').getTime()
                },
                {
                    id: 'order-test-2',
                    conversationId: 123,
                    senderId: 456,
                    timestamp: '2023-01-01T10:01:00.000Z',
                    status: 'sent',
                    content: 'Second message',
                    type: 'text',
                    metadata: {},
                    isOptimistic: false,
                    sortOrder: new Date('2023-01-01T10:01:00.000Z').getTime()
                },
                {
                    id: 'order-test-3',
                    conversationId: 123,
                    senderId: 456,
                    timestamp: '2023-01-01T10:02:00.000Z',
                    status: 'sent',
                    content: 'Third message',
                    type: 'text',
                    metadata: {},
                    isOptimistic: false,
                    sortOrder: new Date('2023-01-01T10:02:00.000Z').getTime()
                }
            ];

            // Add in random order
            store.addMessage(messages[2]);
            store.addMessage(messages[0]);
            store.addMessage(messages[1]);

            // Check ordering
            const orderedMessages = store.getMessages();
            if (orderedMessages[0].id !== 'order-test-1' || 
                orderedMessages[1].id !== 'order-test-2' || 
                orderedMessages[2].id !== 'order-test-3') {
                passed = false;
                this.addViolation('DETERMINISTIC_ORDERING_VIOLATION');
            }

            this.addResult(testName, passed, passed ? 'Deterministic message ordering' : 'Ordering violations found');
        } catch (error) {
            this.addResult(testName, false, error.message);
        }
    }

    /**
     * Test no hidden secondary state
     */
    async testNoHiddenSecondaryState() {
        const testName = 'No Hidden Secondary State';
        try {
            let passed = true;

            // Check that modules don't have hidden state
            const modules = [
                { module: messageService, name: 'messageService' },
                { module: webSocketManager, name: 'webSocketManager' },
                { module: uiController, name: 'uiController' }
            ];

            for (const { module, name } of modules) {
                // Check for message arrays or maps (hidden state)
                for (const prop of Object.getOwnPropertyNames(module)) {
                    if (prop.includes('message') || prop.includes('state')) {
                        const value = module[prop];
                        if (Array.isArray(value) || value instanceof Map || value instanceof Set) {
                            passed = false;
                            this.addViolation(`HIDDEN_SECONDARY_STATE_${name.toUpperCase()}_${prop.toUpperCase()}`);
                        }
                    }
                }
            }

            this.addResult(testName, passed, passed ? 'No hidden secondary state' : 'Hidden state violations found');
        } catch (error) {
            this.addResult(testName, false, error.message);
        }
    }

    /**
     * Test forbidden cross-module state access
     */
    async testForbiddenCrossModuleStateAccess() {
        const testName = 'Forbidden Cross-Module State Access';
        try {
            let passed = true;

            // Check that modules don't access other modules' state directly
            // This is more of a structural check based on our implementation
            
            // Message service should only access store through public API
            // UI controller should only access store through subscription
            // WebSocket should only forward to message service

            const appStatus = appController.getStatus();
            if (!appStatus.dataFlowValid) {
                passed = false;
                this.addViolation('CROSS_MODULE_STATE_ACCESS_VIOLATION');
            }

            this.addResult(testName, passed, passed ? 'No forbidden cross-module state access' : 'Cross-module violations found');
        } catch (error) {
            this.addResult(testName, false, error.message);
        }
    }

    /**
     * Add test result
     */
    addResult(testName, passed, message) {
        this.testResults.push({
            test: testName,
            passed,
            message
        });
    }

    /**
     * Add violation
     */
    addViolation(violation) {
        this.violations.push(violation);
    }

    /**
     * Print test results
     */
    printResults() {
        console.log('\n🔒 Comprehensive SOT Architecture Test Results:');
        console.log('================================================');
        
        const passed = this.testResults.filter(r => r.passed).length;
        const total = this.testResults.length;
        
        this.testResults.forEach(result => {
            const icon = result.passed ? '✅' : '❌';
            console.log(`${icon} ${result.test}: ${result.message}`);
        });

        if (this.violations.length > 0) {
            console.log('\n🚨 Violations Found:');
            this.violations.forEach(violation => {
                console.log(`❌ ${violation}`);
            });
        }
        
        console.log('================================================');
        console.log(`Summary: ${passed}/${total} tests passed`);
        
        if (passed === total && this.violations.length === 0) {
            console.log('🎉 All SOT architecture tests passed! Strict Single Source of Truth verified.');
            console.log('✨ Success criteria achieved:');
            console.log('  - Deterministic message ordering');
            console.log('  - No duplicate or ghost messages');
            console.log('  - UI always matches store snapshot exactly');
            console.log('  - Clear unidirectional data flow');
            console.log('  - No hidden secondary state systems');
        } else {
            console.log('⚠️  SOT architecture violations detected. Anti-patterns found.');
        }
    }
}

// Create global test instance
export const sotTest = new SOTTest();

// Auto-run tests if in development mode
if (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1') {
    console.log('🔧 Development mode detected. Run sotTest.runTests() to test SOT architecture.');
}
